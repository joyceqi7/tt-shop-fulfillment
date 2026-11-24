"""发货编排 smoke test（不依赖真实 TT API）。

用 FakeTTSClient 替代真实 TTSClient，覆盖 5 个关键场景：

1. SHIP 成功：2 条 line 都发码 → codes used / lines shipped / 2 条 fulfillment_log
2. 幂等：对同一 order 再跑一次 fulfill_order_once → 0 新发货
3. 库存不足：清空 free 码再发一单 → status=no_shippable, line.failed_reason=no_stock
4. Redeem API 抛异常：释放 locked 码回到 free，异常冒泡
5. CANCEL：订单变 CANCELLED → 已 used 码作废 / 已 locked 码 release

每个场景跑完做 cleanup，互不污染。
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from redis.asyncio import Redis  # noqa: E402
from sqlalchemy import delete, func, select  # noqa: E402

from app.auth.crypto import encrypt  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db.session import session_factory  # noqa: E402
from app.models.code import Code  # noqa: E402
from app.models.fulfillment_log import FulfillmentLog  # noqa: E402
from app.models.order import TTSOrder, TTSOrderLine  # noqa: E402
from app.models.shop import TTSShop  # noqa: E402
from app.models.sku_mapping import SkuMapping  # noqa: E402
from app.services import code_inventory  # noqa: E402
from app.services.fulfillment import fulfill_order_once  # noqa: E402
from app.services.order_lock import order_lock  # noqa: E402


SHOP_ID = "smoke-shop-001"
SHOP_CIPHER = "smoke-cipher-xxx"
INTERNAL_SKU = "INT-SKU-A"
SELLER_SKU = "SELLER-SKU-A"
TT_PRODUCT_ID = "P-A"
TT_SKU_ID = "S-A"
FACE_VALUE = 1000  # $10.00


class FakeTTSClient:
    """替代真实 TTSClient，可注入想返回的订单详情 & redeem 回复。"""

    def __init__(self) -> None:
        self.order_detail_resp: dict[str, Any] | None = None
        self.redeem_resp: dict[str, Any] = {"code": 0, "message": "ok", "request_id": "fake-req"}
        self.redeem_exc: Exception | None = None
        self.get_order_calls: list[tuple[str, list[str]]] = []
        self.redeem_calls: list[tuple[str, str, list[dict]]] = []

    async def get_order_detail(
        self, shop_cipher: str, order_ids: list[str]
    ) -> dict[str, Any]:
        self.get_order_calls.append((shop_cipher, list(order_ids)))
        assert self.order_detail_resp is not None, "未设置 order_detail_resp"
        return self.order_detail_resp

    async def redeem_info_callback(
        self,
        shop_cipher: str,
        order_id: str,
        order_info_list: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self.redeem_calls.append((shop_cipher, order_id, list(order_info_list)))
        if self.redeem_exc is not None:
            raise self.redeem_exc
        return self.redeem_resp


def _mk_order_payload(order_id: str, status: str, line_ids: list[str]) -> dict:
    return {
        "data": {
            "orders": [
                {
                    "id": order_id,
                    "status": status,
                    "create_time": 1776840000,
                    "update_time": 1776840500,
                    "payment": {"total_amount": "20.00", "currency": "USD"},
                    "line_items": [
                        {
                            "id": lid,
                            "seller_sku": SELLER_SKU,
                            "product_id": TT_PRODUCT_ID,
                            "sku_id": TT_SKU_ID,
                            "quantity": 1,
                            "sale_price": "10.00",
                            "currency": "USD",
                        }
                        for lid in line_ids
                    ],
                }
            ]
        },
        "code": 0,
        "message": "ok",
        "request_id": "fake-detail-req",
    }


async def _prepare_fixtures(n_codes: int = 5) -> None:
    """清理 + 建基础 fixture：shop、sku_mapping、n_codes 张 free 码。"""
    sf = session_factory()

    # 清理所有测试数据（按外键顺序）
    async with sf() as s:
        async with s.begin():
            await s.execute(
                delete(FulfillmentLog).where(FulfillmentLog.shop_id == SHOP_ID)
            )
            await s.execute(
                delete(TTSOrderLine).where(
                    TTSOrderLine.order_id.in_(
                        select(TTSOrder.order_id).where(TTSOrder.shop_id == SHOP_ID)
                    )
                )
            )
            await s.execute(delete(TTSOrder).where(TTSOrder.shop_id == SHOP_ID))
            await s.execute(delete(Code).where(Code.sku_id == INTERNAL_SKU))
            await s.execute(
                delete(SkuMapping).where(SkuMapping.internal_sku_id == INTERNAL_SKU)
            )
            await s.execute(delete(TTSShop).where(TTSShop.shop_id == SHOP_ID))

    # 建新 fixture
    async with sf() as s:
        async with s.begin():
            s.add(
                TTSShop(
                    shop_id=SHOP_ID,
                    shop_name="Smoke Shop",
                    shop_cipher=SHOP_CIPHER,
                    region="US",
                )
            )
            s.add(
                SkuMapping(
                    internal_sku_id=INTERNAL_SKU,
                    seller_sku=SELLER_SKU,
                    tts_product_id=TT_PRODUCT_ID,
                    tts_sku_id=TT_SKU_ID,
                    face_value=FACE_VALUE,
                    active=True,
                )
            )
        async with s.begin():
            batch_id = str(uuid.uuid4())
            for i in range(n_codes):
                plain = f"SMOKE-CODE-{uuid.uuid4().hex[:8].upper()}"
                s.add(
                    Code(
                        batch_id=batch_id,
                        sku_id=INTERNAL_SKU,
                        face_value=FACE_VALUE,
                        code_encrypted=encrypt(plain),
                        code_hash=f"smoke-hash-{i}-{uuid.uuid4().hex}",
                        status="free",
                        operator="smoke",
                    )
                )


async def _count_free() -> int:
    sf = session_factory()
    async with sf() as s:
        return await code_inventory.count_free(
            s, sku_id=INTERNAL_SKU, face_value=FACE_VALUE
        )


async def _code_status_counts() -> dict[str, int]:
    sf = session_factory()
    async with sf() as s:
        rows = (
            await s.execute(
                select(Code.status, func.count(Code.id))
                .where(Code.sku_id == INTERNAL_SKU)
                .group_by(Code.status)
            )
        ).all()
    return {status: int(cnt) for status, cnt in rows}


async def _fulfillment_log_count(order_id: str) -> int:
    sf = session_factory()
    async with sf() as s:
        return int(
            (
                await s.execute(
                    select(func.count(FulfillmentLog.id)).where(
                        FulfillmentLog.order_id == order_id
                    )
                )
            ).scalar_one()
        )


async def _line_statuses(order_id: str) -> list[tuple[str, str, str | None]]:
    sf = session_factory()
    async with sf() as s:
        rows = (
            await s.execute(
                select(
                    TTSOrderLine.order_line_id,
                    TTSOrderLine.status,
                    TTSOrderLine.failed_reason,
                ).where(TTSOrderLine.order_id == order_id)
            )
        ).all()
    return [(r[0], r[1], r[2]) for r in rows]


async def _run_fulfill(client: FakeTTSClient, order_id: str) -> dict:
    settings = get_settings()
    sf = session_factory()
    redis = Redis.from_url(settings.redis_url)
    try:
        async with order_lock(redis, order_id):
            async with sf() as s:
                shop = await s.get(TTSShop, SHOP_ID)
            return await fulfill_order_once(
                session_factory=sf, client=client, shop=shop, order_id=order_id
            )
    finally:
        await redis.aclose()


async def case_1_ship_success() -> None:
    print("=" * 60)
    print("Case 1: SHIP 成功 — 2 line 全发码")
    print("=" * 60)

    await _prepare_fixtures(n_codes=3)
    order_id = f"smoke-ord-{uuid.uuid4().hex[:8]}"
    line_ids = [f"{order_id}-L1", f"{order_id}-L2"]

    client = FakeTTSClient()
    client.order_detail_resp = _mk_order_payload(order_id, "AWAITING_SHIPMENT", line_ids)

    result = await _run_fulfill(client, order_id)
    print(f"  result={result}")
    assert result["status"] == "shipped"
    assert result["shipped_count"] == 2

    counts = await _code_status_counts()
    print(f"  code status counts: {counts}")
    assert counts.get("used") == 2 and counts.get("free") == 1

    lines = await _line_statuses(order_id)
    print(f"  lines: {lines}")
    assert all(s == "shipped" for (_, s, _) in lines)

    log_cnt = await _fulfillment_log_count(order_id)
    print(f"  fulfillment_log rows for order: {log_cnt}")
    assert log_cnt == 2, f"expected 2 (detail+redeem), got {log_cnt}"

    assert len(client.redeem_calls) == 1
    assert len(client.redeem_calls[0][2]) == 2
    print("  OK")
    print()


async def case_2_idempotent() -> None:
    print("=" * 60)
    print("Case 2: 重复 fulfill 同一 order — 幂等")
    print("=" * 60)

    order_id = f"smoke-ord-{uuid.uuid4().hex[:8]}"
    line_ids = [f"{order_id}-L1"]

    await _prepare_fixtures(n_codes=3)

    client = FakeTTSClient()
    client.order_detail_resp = _mk_order_payload(order_id, "AWAITING_SHIPMENT", line_ids)
    r1 = await _run_fulfill(client, order_id)
    assert r1["status"] == "shipped" and r1["shipped_count"] == 1
    print(f"  first call: {r1}")

    # 第二次
    client2 = FakeTTSClient()
    client2.order_detail_resp = _mk_order_payload(order_id, "AWAITING_SHIPMENT", line_ids)
    r2 = await _run_fulfill(client2, order_id)
    print(f"  second call: {r2}")
    assert r2["status"] == "already_shipped"
    assert r2["shipped_count"] == 0
    assert len(client2.redeem_calls) == 0, "幂等时不应再调 redeem"

    counts = await _code_status_counts()
    assert counts.get("used") == 1 and counts.get("free") == 2
    print(f"  code status counts: {counts}")
    print("  OK")
    print()


async def case_3_no_stock() -> None:
    print("=" * 60)
    print("Case 3: 库存不足 — line 标 failed(no_stock)")
    print("=" * 60)

    await _prepare_fixtures(n_codes=0)  # 0 张 free
    order_id = f"smoke-ord-{uuid.uuid4().hex[:8]}"
    line_ids = [f"{order_id}-L1"]

    client = FakeTTSClient()
    client.order_detail_resp = _mk_order_payload(order_id, "AWAITING_SHIPMENT", line_ids)

    result = await _run_fulfill(client, order_id)
    print(f"  result={result}")
    assert result["status"] == "no_shippable"
    assert result["skipped"][0]["reason"] == "no_stock"

    lines = await _line_statuses(order_id)
    print(f"  lines: {lines}")
    assert lines[0][1] == "failed" and lines[0][2] == "no_stock"

    assert len(client.redeem_calls) == 0
    print("  OK")
    print()


async def case_4_redeem_api_fail_rollback() -> None:
    print("=" * 60)
    print("Case 4: Redeem API 抛异常 — locked 码应全部 release 回 free")
    print("=" * 60)

    await _prepare_fixtures(n_codes=3)
    order_id = f"smoke-ord-{uuid.uuid4().hex[:8]}"
    line_ids = [f"{order_id}-L1", f"{order_id}-L2"]

    client = FakeTTSClient()
    client.order_detail_resp = _mk_order_payload(order_id, "AWAITING_SHIPMENT", line_ids)
    client.redeem_exc = RuntimeError("simulated network error")

    free_before = await _count_free()
    assert free_before == 3

    raised = False
    try:
        await _run_fulfill(client, order_id)
    except RuntimeError as e:
        raised = True
        print(f"  exception bubbled up as expected: {e}")
    assert raised

    counts = await _code_status_counts()
    print(f"  code status counts: {counts}")
    assert counts.get("free") == 3, f"expected 3 free after rollback, got {counts}"
    assert counts.get("locked", 0) == 0

    # fulfillment_log 应有 2 条：detail + redeem(失败，error 非空)
    sf = session_factory()
    async with sf() as s:
        log_rows = (
            await s.execute(
                select(FulfillmentLog.api_endpoint, FulfillmentLog.error).where(
                    FulfillmentLog.order_id == order_id
                )
            )
        ).all()
    print(f"  fulfillment_log: {log_rows}")
    assert any(r[1] and "simulated" in r[1] for r in log_rows), "redeem 失败日志应写入"

    print("  OK")
    print()


async def case_5_cancel() -> None:
    print("=" * 60)
    print("Case 5: CANCEL — 已 used 码作废、已 locked 码 release")
    print("=" * 60)

    await _prepare_fixtures(n_codes=3)
    order_id = f"smoke-ord-{uuid.uuid4().hex[:8]}"
    line_ids = [f"{order_id}-L1"]

    # 先发货成功 → 1 张 used
    client = FakeTTSClient()
    client.order_detail_resp = _mk_order_payload(order_id, "AWAITING_SHIPMENT", line_ids)
    r1 = await _run_fulfill(client, order_id)
    assert r1["status"] == "shipped"

    # 订单变 CANCELLED
    client2 = FakeTTSClient()
    client2.order_detail_resp = _mk_order_payload(order_id, "CANCELLED", line_ids)
    r2 = await _run_fulfill(client2, order_id)
    print(f"  cancel result: {r2}")
    assert r2["status"] == "cancelled"
    assert r2["voided"] == 1
    assert r2["released"] == 0

    counts = await _code_status_counts()
    print(f"  code status counts: {counts}")
    assert counts.get("void") == 1 and counts.get("free") == 2

    lines = await _line_statuses(order_id)
    print(f"  lines: {lines}")
    assert lines[0][1] == "cancelled"

    print("  OK")
    print()


async def main() -> None:
    await case_1_ship_success()
    await case_2_idempotent()
    await case_3_no_stock()
    await case_4_redeem_api_fail_rollback()
    await case_5_cancel()

    # 最终清理
    await _prepare_fixtures(n_codes=0)
    sf = session_factory()
    async with sf() as s:
        async with s.begin():
            await s.execute(delete(TTSShop).where(TTSShop.shop_id == SHOP_ID))

    print("=" * 60)
    print("全部通过 OK — fulfill 主流程 + 幂等 + 无货 + API 失败回滚 + CANCEL 贯通")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
