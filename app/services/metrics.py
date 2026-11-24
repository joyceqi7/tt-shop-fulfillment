"""业务指标快照收集。

设计：
- 所有指标都是"查 DB/Redis 算一次"的 **snapshot gauge**，不在代码路径里累计 counter
  （因为 webhook receiver / arq worker / FastAPI 是多进程，counter 聚合要 Push Gateway 或
  `PROMETHEUS_MULTIPROC_DIR`，MVP 不值当）
- `/metrics` 每次 scrape（Prometheus 默认 15s 一次）调一次 `collect_snapshot`，
  每次 ~6 个 DB 查询 + 若干 Redis GET，轻量不压 DB
- `watchdog_task`（arq cron）复用同一个 snapshot，对超阈值的指标发飞书告警

指标字典：

| name                                       | type  | SLO                          |
|--------------------------------------------|-------|------------------------------|
| tts_webhook_events_total{status}           | gauge | dead=0 / retrying 不堆积     |
| tts_codes_free{sku_id}                     | gauge | 高于 stock_alert_threshold   |
| tts_codes_stuck_locked_total               | gauge | =0（超 code_lock_timeout 的 locked） |
| tts_order_lines_pending_total{status}      | gauge | failed 不堆积                |
| tts_reconciliation_lag_seconds{shop_id}    | gauge | <1800s                       |
| tts_token_expire_in_seconds{app_key}       | gauge | >3600s                       |
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.models.code import Code
from app.models.order import TTSOrderLine
from app.models.shop import TTSShop
from app.models.token import TTSToken
from app.models.webhook_event import WebhookEvent
from app.services.reconciliation import LAST_TS_KEY_TPL


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MetricSnapshot:
    collected_at: int  # unix ts
    webhook_events_by_status: dict[str, int] = field(default_factory=dict)
    codes_free_by_sku: dict[str, int] = field(default_factory=dict)
    codes_stuck_locked: int = 0
    order_lines_pending_by_status: dict[str, int] = field(default_factory=dict)
    reconciliation_lag_seconds: dict[str, float] = field(default_factory=dict)
    """shop_id → now - last_ts；-1 表示该店从未跑过对账。"""
    token_expire_in_seconds: dict[str, float] = field(default_factory=dict)
    """app_key → expire_at - now；负数表示已过期。"""


async def collect_snapshot(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    redis: Redis,
) -> MetricSnapshot:
    settings = get_settings()
    now_ts = int(time.time())
    snap = MetricSnapshot(collected_at=now_ts)

    stuck_cutoff = datetime.now(tz=timezone.utc) - timedelta(
        seconds=settings.code_lock_timeout_seconds
    )

    async with session_factory() as s:
        r1 = await s.execute(
            select(WebhookEvent.status, func.count()).group_by(WebhookEvent.status)
        )
        snap.webhook_events_by_status = {row[0]: int(row[1]) for row in r1.all()}

        r2 = await s.execute(
            select(Code.sku_id, func.count())
            .where(Code.status == "free")
            .group_by(Code.sku_id)
        )
        snap.codes_free_by_sku = {row[0]: int(row[1]) for row in r2.all()}

        r3 = await s.execute(
            select(func.count()).where(
                Code.status == "locked", Code.locked_at < stuck_cutoff
            )
        )
        snap.codes_stuck_locked = int(r3.scalar_one())

        r4 = await s.execute(
            select(TTSOrderLine.status, func.count())
            .where(TTSOrderLine.status.in_(["pending", "failed"]))
            .group_by(TTSOrderLine.status)
        )
        snap.order_lines_pending_by_status = {row[0]: int(row[1]) for row in r4.all()}

        r5 = await s.execute(
            select(TTSToken.app_key, TTSToken.access_token_expire_at)
        )
        snap.token_expire_in_seconds = {
            row[0]: float(row[1] - now_ts) for row in r5.all()
        }

        r6 = await s.execute(select(TTSShop.shop_id))
        shop_ids = [row[0] for row in r6.all()]

    for shop_id in shop_ids:
        raw = await redis.get(LAST_TS_KEY_TPL.format(shop_id=shop_id))
        if raw is None:
            snap.reconciliation_lag_seconds[shop_id] = -1.0
        else:
            try:
                snap.reconciliation_lag_seconds[shop_id] = float(now_ts - int(raw))
            except (TypeError, ValueError):
                snap.reconciliation_lag_seconds[shop_id] = -1.0

    logger.debug(
        "metric snapshot: webhook=%s free_skus=%d stuck=%d",
        snap.webhook_events_by_status,
        len(snap.codes_free_by_sku),
        snap.codes_stuck_locked,
    )
    return snap


def render_prometheus(snap: MetricSnapshot) -> bytes:
    """把 snapshot 渲染成 Prometheus exposition format。"""
    from prometheus_client import CollectorRegistry, Gauge, generate_latest

    reg = CollectorRegistry()

    g_wh = Gauge(
        "tts_webhook_events_total",
        "webhook_events count by status",
        ["status"],
        registry=reg,
    )
    for k, v in snap.webhook_events_by_status.items():
        g_wh.labels(status=k).set(v)

    g_free = Gauge(
        "tts_codes_free",
        "number of free codes per sku",
        ["sku_id"],
        registry=reg,
    )
    for k, v in snap.codes_free_by_sku.items():
        g_free.labels(sku_id=k).set(v)

    g_stuck = Gauge(
        "tts_codes_stuck_locked_total",
        "codes that stayed locked beyond code_lock_timeout_seconds",
        registry=reg,
    )
    g_stuck.set(snap.codes_stuck_locked)

    g_line = Gauge(
        "tts_order_lines_pending_total",
        "order lines not shipped yet, by status (pending / failed)",
        ["status"],
        registry=reg,
    )
    for k, v in snap.order_lines_pending_by_status.items():
        g_line.labels(status=k).set(v)

    g_lag = Gauge(
        "tts_reconciliation_lag_seconds",
        "now - reconciliation last_ts per shop (-1 means never run)",
        ["shop_id"],
        registry=reg,
    )
    for k, v in snap.reconciliation_lag_seconds.items():
        g_lag.labels(shop_id=k).set(v)

    g_tok = Gauge(
        "tts_token_expire_in_seconds",
        "access_token remaining seconds (negative if already expired)",
        ["app_key"],
        registry=reg,
    )
    for k, v in snap.token_expire_in_seconds.items():
        g_tok.labels(app_key=k).set(v)

    return generate_latest(reg)


def evaluate_alerts(snap: MetricSnapshot) -> list[str]:
    """把 snapshot 和阈值比较，返回一组告警消息（每条独立）。

    空列表表示没要报的。
    """
    s = get_settings()
    alerts: list[str] = []

    # 1. 死信堆积
    dead = snap.webhook_events_by_status.get("dead", 0)
    if dead > 0:
        alerts.append(f"webhook_events.status=dead 堆积 {dead} 条（应为 0）")

    # 2. 低水位（按 sku_id 维度）
    for sku_id, free in snap.codes_free_by_sku.items():
        if free < s.stock_alert_threshold:
            alerts.append(
                f"卡密水位告警 sku={sku_id} free={free} < 阈值 {s.stock_alert_threshold}"
            )

    # 3. 僵尸锁
    if snap.codes_stuck_locked > 0:
        alerts.append(
            f"codes stuck_locked={snap.codes_stuck_locked}（locked > {s.code_lock_timeout_seconds}s，可能 worker 崩溃）"
        )

    # 4. pending line 堆积
    failed_lines = snap.order_lines_pending_by_status.get("failed", 0)
    if failed_lines > 0:
        alerts.append(
            f"tts_order_lines.status=failed 堆积 {failed_lines} 条（检查 fulfillment_log）"
        )

    # 5. 对账延迟
    for shop_id, lag in snap.reconciliation_lag_seconds.items():
        if lag < 0:
            continue  # 从未跑过，不告警（初始状态）
        if lag > s.reconciliation_lag_alert_seconds:
            alerts.append(
                f"reconciliation 滞后告警 shop={shop_id} lag={int(lag)}s > {s.reconciliation_lag_alert_seconds}s"
            )

    # 6. token 即将过期
    for app_key, remain in snap.token_expire_in_seconds.items():
        if remain < s.token_expire_alert_seconds:
            alerts.append(
                f"access_token 即将过期告警 app_key={app_key} 剩余 {int(remain)}s < {s.token_expire_alert_seconds}s"
            )

    return alerts
