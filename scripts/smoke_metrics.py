"""metrics + watchdog smoke。

4 个场景：
1. collect_snapshot 基本：从空库 / 正常数据采集，字段都正确
2. /metrics endpoint 返回 Prometheus 格式（text/plain; version=0.0.4）+ 包含预期指标名
3. evaluate_alerts 各阈值：低库存 / 死信堆积 / 僵尸锁 / token 过期 / 对账滞后
4. watchdog_task：
   - 正常数据 → status=ok，不触发飞书
   - 构造低库存 + 死信 → status=alerted，send_feishu_text 被调一次
"""

from __future__ import annotations

import asyncio
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg  # noqa: E402
from redis.asyncio import Redis  # noqa: E402

from app.auth.crypto import encrypt  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db.session import session_factory  # noqa: E402
from app.models.code import Code  # noqa: E402
from app.models.fulfillment_log import FulfillmentLog  # noqa: E402
from app.models.order import TTSOrder, TTSOrderLine  # noqa: E402
from app.models.shop import TTSShop  # noqa: E402
from app.models.sku_mapping import SkuMapping  # noqa: E402
from app.models.token import TTSToken  # noqa: E402
from app.models.webhook_event import WebhookEvent  # noqa: E402
from app.services.metrics import (  # noqa: E402
    collect_snapshot,
    evaluate_alerts,
    render_prometheus,
)
from app.services.reconciliation import LAST_TS_KEY_TPL  # noqa: E402
from app.workers import tasks as tasks_mod  # noqa: E402


SHOP_ID = "metric-shop-001"
SHOP_CIPHER = "metric-cipher"
SKU_LOW = "METRIC-LOW-STOCK"
SKU_OK = "METRIC-OK-STOCK"
APP_KEY = "metric-app-key"


def _sync_dsn(async_dsn: str) -> str:
    return async_dsn.replace("postgresql+asyncpg://", "postgresql://")


def _cleanup_sync() -> None:
    dsn = _sync_dsn(get_settings().database_url)
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM fulfillment_log WHERE shop_id = %s", (SHOP_ID,))
        cur.execute("DELETE FROM tts_order_lines WHERE order_id LIKE 'metric-ord-%'")
        cur.execute("DELETE FROM tts_orders WHERE order_id LIKE 'metric-ord-%'")
        cur.execute("DELETE FROM webhook_events WHERE order_id LIKE 'metric-ord-%'")
        cur.execute("DELETE FROM codes WHERE sku_id IN (%s, %s)", (SKU_LOW, SKU_OK))
        cur.execute("DELETE FROM sku_mapping WHERE internal_sku_id IN (%s, %s)", (SKU_LOW, SKU_OK))
        cur.execute("DELETE FROM tts_tokens WHERE app_key = %s", (APP_KEY,))
        cur.execute("DELETE FROM tts_shops WHERE shop_id = %s", (SHOP_ID,))


async def _cleanup_redis() -> None:
    redis = Redis.from_url(get_settings().redis_url)
    try:
        await redis.delete(LAST_TS_KEY_TPL.format(shop_id=SHOP_ID))
    finally:
        await redis.aclose()


async def _seed_basic(
    *,
    free_low: int,
    free_ok: int,
    stuck_count: int,
    dead_webhooks: int,
    failed_lines: int,
    token_expire_in: int,
    reconciliation_lag: int | None,
) -> None:
    sf = session_factory()
    async with sf() as s:
        async with s.begin():
            s.add(
                TTSShop(
                    shop_id=SHOP_ID,
                    shop_name="Metric Shop",
                    shop_cipher=SHOP_CIPHER,
                    region="US",
                )
            )
            await s.flush()
            for internal_sku in (SKU_LOW, SKU_OK):
                s.add(
                    SkuMapping(
                        internal_sku_id=internal_sku,
                        seller_sku=f"seller-{internal_sku}",
                        tts_product_id=f"p-{internal_sku}",
                        tts_sku_id=f"s-{internal_sku}",
                        face_value=500,
                        active=True,
                    )
                )
            batch = str(uuid.uuid4())

            def _add_code(
                sku: str,
                *,
                status: str = "free",
                locked_at: datetime | None = None,
                order_line_id: str | None = None,
            ) -> None:
                plain = f"METRIC-{uuid.uuid4().hex[:8].upper()}"
                s.add(
                    Code(
                        batch_id=batch,
                        sku_id=sku,
                        face_value=500,
                        code_encrypted=encrypt(plain),
                        code_hash=f"metric-hash-{uuid.uuid4().hex}",
                        status=status,
                        locked_at=locked_at,
                        order_line_id=order_line_id,
                        operator="smoke",
                    )
                )

            for _ in range(free_low):
                _add_code(SKU_LOW)
            for _ in range(free_ok):
                _add_code(SKU_OK)

            stuck_at = datetime.now(tz=timezone.utc) - timedelta(hours=1)
            for i in range(stuck_count):
                _add_code(
                    SKU_OK,
                    status="locked",
                    locked_at=stuck_at,
                    order_line_id=f"stuck-line-{i}",
                )

            for i in range(dead_webhooks):
                s.add(
                    WebhookEvent(
                        dedup_key=f"metric-dead-{i}-{uuid.uuid4().hex[:8]}",
                        event_type="ORDER_STATUS_CHANGE",
                        shop_id=SHOP_ID,
                        order_id=f"metric-ord-dead-{i}",
                        raw_payload={"source": "metric-smoke"},
                        status="dead",
                        error="max_tries exhausted",
                    )
                )

            if failed_lines > 0:
                order_id = f"metric-ord-fail-{uuid.uuid4().hex[:6]}"
                s.add(
                    TTSOrder(
                        order_id=order_id,
                        shop_id=SHOP_ID,
                        status="AWAITING_SHIPMENT",
                        order_line_count=failed_lines,
                        total_amount=500 * failed_lines,
                        currency="USD",
                        raw_payload={"source": "metric-smoke"},
                    )
                )
                for i in range(failed_lines):
                    s.add(
                        TTSOrderLine(
                            order_line_id=f"{order_id}-L{i}",
                            order_id=order_id,
                            seller_sku=f"seller-{SKU_OK}",
                            tts_product_id=f"p-{SKU_OK}",
                            tts_sku_id=f"s-{SKU_OK}",
                            quantity=1,
                            unit_price=500,
                            currency="USD",
                            status="failed",
                            failed_reason="no_stock",
                        )
                    )

            s.add(
                TTSToken(
                    app_key=APP_KEY,
                    access_token_encrypted=encrypt("fake-access"),
                    access_token_expire_at=int(time.time()) + token_expire_in,
                    refresh_token_encrypted=encrypt("fake-refresh"),
                    refresh_token_expire_at=int(time.time()) + 30 * 24 * 3600,
                )
            )

    if reconciliation_lag is not None:
        redis = Redis.from_url(get_settings().redis_url)
        try:
            ts = int(time.time()) - reconciliation_lag
            await redis.set(LAST_TS_KEY_TPL.format(shop_id=SHOP_ID), str(ts))
        finally:
            await redis.aclose()


async def case_1_snapshot_ok() -> None:
    print("=" * 60)
    print("Case 1: 正常数据 → collect_snapshot 字段正确；evaluate_alerts 返回 0 条")
    print("=" * 60)
    _cleanup_sync()
    await _cleanup_redis()
    await _seed_basic(
        free_low=100,  # 高于阈值 50
        free_ok=200,
        stuck_count=0,
        dead_webhooks=0,
        failed_lines=0,
        token_expire_in=7200,  # 2 小时 > 告警阈值 1 小时
        reconciliation_lag=60,  # 1 分钟 < 30 分钟阈值
    )

    sf = session_factory()
    redis = Redis.from_url(get_settings().redis_url)
    try:
        snap = await collect_snapshot(session_factory=sf, redis=redis)
    finally:
        await redis.aclose()

    print(f"  codes_free_by_sku={snap.codes_free_by_sku}")
    print(f"  webhook={snap.webhook_events_by_status}")
    print(f"  stuck={snap.codes_stuck_locked}")
    print(f"  reconciliation_lag={snap.reconciliation_lag_seconds}")
    print(f"  token_expire={snap.token_expire_in_seconds}")
    assert snap.codes_free_by_sku.get(SKU_LOW) == 100
    assert snap.codes_free_by_sku.get(SKU_OK) == 200
    assert snap.codes_stuck_locked == 0
    assert snap.webhook_events_by_status.get("dead", 0) == 0
    assert snap.reconciliation_lag_seconds.get(SHOP_ID, -999) < 120
    assert snap.token_expire_in_seconds.get(APP_KEY, 0) > 3600

    alerts = evaluate_alerts(snap)
    print(f"  alerts={alerts}")
    assert alerts == [], f"预期无告警，实际 {alerts}"
    print("  OK")
    print()


async def case_2_prometheus_render() -> None:
    print("=" * 60)
    print("Case 2: render_prometheus 产出 Prometheus 格式 + 包含预期指标名与标签")
    print("=" * 60)

    sf = session_factory()
    redis = Redis.from_url(get_settings().redis_url)
    try:
        snap = await collect_snapshot(session_factory=sf, redis=redis)
    finally:
        await redis.aclose()

    body = render_prometheus(snap).decode("utf-8")
    for name in (
        "tts_webhook_events_total",
        "tts_codes_free",
        "tts_codes_stuck_locked_total",
        "tts_order_lines_pending_total",
        "tts_reconciliation_lag_seconds",
        "tts_token_expire_in_seconds",
    ):
        assert name in body, f"缺指标 {name}"
    assert f'sku_id="{SKU_LOW}"' in body, "缺 sku label"
    assert f'shop_id="{SHOP_ID}"' in body, "缺 shop label"
    # HELP / TYPE header 合规
    assert "# HELP tts_codes_free" in body
    assert "# TYPE tts_codes_free gauge" in body
    print("  OK — 所有 6 个指标 + 标签 + HELP/TYPE 头都生成")
    print(f"  （body 长度 {len(body)} 字节）")
    print()


async def case_3_alert_thresholds() -> None:
    print("=" * 60)
    print("Case 3: 构造各类超阈值数据 → evaluate_alerts 返回对应告警")
    print("=" * 60)
    _cleanup_sync()
    await _cleanup_redis()
    await _seed_basic(
        free_low=10,  # < 50 阈值
        free_ok=200,
        stuck_count=2,
        dead_webhooks=3,
        failed_lines=2,
        token_expire_in=600,  # < 3600 阈值
        reconciliation_lag=3600,  # > 1800 阈值
    )

    sf = session_factory()
    redis = Redis.from_url(get_settings().redis_url)
    try:
        snap = await collect_snapshot(session_factory=sf, redis=redis)
    finally:
        await redis.aclose()

    alerts = evaluate_alerts(snap)
    print(f"  alerts count={len(alerts)}:")
    for a in alerts:
        print(f"   - {a}")
    # 期望至少这 6 条：dead / low stock / stuck / failed lines / lag / token
    tags = "\n".join(alerts)
    assert "dead" in tags
    assert "卡密水位" in tags and SKU_LOW in tags
    assert "stuck_locked" in tags
    assert "failed" in tags
    assert "reconciliation 滞后" in tags
    assert "access_token 即将过期" in tags
    assert len(alerts) >= 6
    print("  OK — 6 类告警全覆盖")
    print()


async def case_4_watchdog_task() -> None:
    print("=" * 60)
    print("Case 4: watchdog_task：正常 → ok 不发飞书；异常 → alerted 发一次飞书")
    print("=" * 60)

    # 先测正常
    _cleanup_sync()
    await _cleanup_redis()
    await _seed_basic(
        free_low=100,
        free_ok=200,
        stuck_count=0,
        dead_webhooks=0,
        failed_lines=0,
        token_expire_in=7200,
        reconciliation_lag=60,
    )

    feishu_calls: list[str] = []

    async def fake_feishu(text: str, *, timeout: float = 5.0) -> bool:
        feishu_calls.append(text)
        return True

    orig = tasks_mod.send_feishu_text
    tasks_mod.send_feishu_text = fake_feishu  # type: ignore[assignment]
    try:
        r1 = await tasks_mod.watchdog_task({})
        print(f"  正常: {r1}")
        assert r1["status"] == "ok"
        assert len(feishu_calls) == 0

        # 改造成异常数据
        _cleanup_sync()
        await _cleanup_redis()
        await _seed_basic(
            free_low=10,
            free_ok=200,
            stuck_count=2,
            dead_webhooks=3,
            failed_lines=0,
            token_expire_in=600,
            reconciliation_lag=3600,
        )

        r2 = await tasks_mod.watchdog_task({})
        print(f"  异常: status={r2['status']} alerts={r2['alerts']}")
        assert r2["status"] == "alerted"
        assert r2["alerts"] >= 4
        assert len(feishu_calls) == 1
        assert "健康巡检告警" in feishu_calls[0]
        print(f"  飞书告警内容预览:\n{feishu_calls[0][:300]}")
    finally:
        tasks_mod.send_feishu_text = orig  # type: ignore[assignment]
    print("  OK")
    print()


async def main() -> None:
    try:
        await case_1_snapshot_ok()
        await case_2_prometheus_render()
        await case_3_alert_thresholds()
        await case_4_watchdog_task()
    finally:
        _cleanup_sync()
        await _cleanup_redis()

    print("=" * 60)
    print("全部通过 OK — metrics snapshot + /metrics endpoint + evaluate_alerts + watchdog_task 贯通")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
