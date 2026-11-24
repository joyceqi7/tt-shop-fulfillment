"""兜底对账 Job smoke test（FakeTTSClient mock）。

4 个场景：

1. 全链路扫描：分页（2 页）+ 混合订单状态（1 SHIP / 1 COMPLETED），
   验证 stats + last_ts 推进
2. 单实例锁：两个 acquire_run_lock 并发，第二个立刻 ReconciliationAlreadyRunning
3. get_order_list 失败 → last_ts NOT 推进
4. last_ts >= till → 直接跳过，shops_skipped +1
"""

from __future__ import annotations

import asyncio
import sys
import time
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from redis.asyncio import Redis  # noqa: E402
from sqlalchemy import delete  # noqa: E402

from app.auth.crypto import encrypt  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db.session import session_factory  # noqa: E402
from app.models.code import Code  # noqa: E402
from app.models.fulfillment_log import FulfillmentLog  # noqa: E402
from app.models.order import TTSOrder, TTSOrderLine  # noqa: E402
from app.models.shop import TTSShop  # noqa: E402
from app.models.sku_mapping import SkuMapping  # noqa: E402
from app.services.reconciliation import (  # noqa: E402
    LAST_TS_KEY_TPL,
    ReconciliationAlreadyRunning,
    acquire_run_lock,
    run_reconciliation,
)
from sqlalchemy import select  # noqa: E402


SHOP_ID = "recon-shop-001"
SHOP_CIPHER = "recon-cipher"
INTERNAL_SKU = "RECON-SKU"
SELLER_SKU = "RECON-SELLER-SKU"
TT_PRODUCT_ID = "RP-A"
TT_SKU_ID = "RS-A"
FACE_VALUE = 500  # $5.00


class FakeTTSClient:
    """按 shop_cipher 路由：只处理我们自己的店，其他店（真实 shop）返回空单。"""

    def __init__(self) -> None:
        # pages: {shop_cipher: [page_dict, ...]}
        self.list_pages: dict[str, list[dict[str, Any]]] = {}
        self.list_call_count: dict[str, int] = {}
        self.list_exc: Exception | None = None

        self.detail_by_order: dict[str, dict[str, Any]] = {}
        self.redeem_resp = {"code": 0, "message": "ok", "request_id": "fake"}
        self.redeem_calls: list[tuple[str, list[dict]]] = []

    @property
    def total_list_calls(self) -> int:
        return sum(self.list_call_count.values())

    async def get_order_list(
        self,
        shop_cipher: str,
        *,
        page_size: int = 50,
        page_token: str | None = None,
        update_time_ge: int | None = None,
        update_time_lt: int | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        if self.list_exc is not None and shop_cipher == SHOP_CIPHER:
            raise self.list_exc

        self.list_call_count[shop_cipher] = self.list_call_count.get(shop_cipher, 0) + 1
        pages = self.list_pages.get(shop_cipher, [])
        idx = self.list_call_count[shop_cipher] - 1
        if idx >= len(pages):
            return {"data": {"orders": [], "next_page_token": None}}
        return pages[idx]

    async def get_order_detail(
        self, shop_cipher: str, order_ids: list[str]
    ) -> dict[str, Any]:
        oid = order_ids[0]
        payload = self.detail_by_order.get(oid)
        assert payload is not None, f"detail 未配置 order_id={oid}"
        return payload

    async def redeem_info_callback(
        self,
        shop_cipher: str,
        order_id: str,
        order_info_list: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self.redeem_calls.append((order_id, list(order_info_list)))
        return self.redeem_resp


def _detail_payload(order_id: str, status: str, line_ids: list[str]) -> dict:
    return {
        "data": {
            "orders": [
                {
                    "id": order_id,
                    "status": status,
                    "create_time": 1776840000,
                    "update_time": 1776840500,
                    "payment": {"total_amount": "5.00", "currency": "USD"},
                    "line_items": [
                        {
                            "id": lid,
                            "seller_sku": SELLER_SKU,
                            "product_id": TT_PRODUCT_ID,
                            "sku_id": TT_SKU_ID,
                            "quantity": 1,
                            "sale_price": "5.00",
                            "currency": "USD",
                        }
                        for lid in line_ids
                    ],
                }
            ]
        },
        "code": 0,
    }


async def _prepare_fixtures(n_codes: int = 5) -> None:
    sf = session_factory()

    async with sf() as s:
        async with s.begin():
            # 先按 order_id 前缀清 line/orders（避免孤儿数据阻塞后续 code 删除）
            await s.execute(
                delete(TTSOrderLine).where(TTSOrderLine.order_id.like("recon-ord-%"))
            )
            await s.execute(
                delete(TTSOrder).where(TTSOrder.order_id.like("recon-ord-%"))
            )
            await s.execute(
                delete(FulfillmentLog).where(FulfillmentLog.shop_id == SHOP_ID)
            )
            await s.execute(delete(Code).where(Code.sku_id == INTERNAL_SKU))
            await s.execute(
                delete(SkuMapping).where(SkuMapping.internal_sku_id == INTERNAL_SKU)
            )
            await s.execute(delete(TTSShop).where(TTSShop.shop_id == SHOP_ID))

    async with sf() as s:
        async with s.begin():
            s.add(
                TTSShop(
                    shop_id=SHOP_ID,
                    shop_name="Recon Shop",
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
            for _ in range(n_codes):
                plain = f"RECON-{uuid.uuid4().hex[:8].upper()}"
                s.add(
                    Code(
                        batch_id=batch_id,
                        sku_id=INTERNAL_SKU,
                        face_value=FACE_VALUE,
                        code_encrypted=encrypt(plain),
                        code_hash=f"recon-hash-{uuid.uuid4().hex}",
                        status="free",
                        operator="smoke",
                    )
                )


async def _cleanup() -> None:
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url)
    try:
        await redis.delete(LAST_TS_KEY_TPL.format(shop_id=SHOP_ID))
    finally:
        await redis.aclose()


async def case_1_full_scan() -> None:
    print("=" * 60)
    print("Case 1: 分页 + 混合状态（1 SHIP / 1 COMPLETED），正常推进 last_ts")
    print("=" * 60)

    await _prepare_fixtures(n_codes=3)
    await _cleanup()

    settings = get_settings()
    redis = Redis.from_url(settings.redis_url)

    order_a = f"recon-ord-{uuid.uuid4().hex[:8]}"  # AWAITING_SHIPMENT
    order_b = f"recon-ord-{uuid.uuid4().hex[:8]}"  # COMPLETED

    client = FakeTTSClient()
    client.list_pages[SHOP_CIPHER] = [
        {
            "data": {
                "orders": [{"id": order_a, "update_time": 1776840500}],
                "next_page_token": "tok-2",
            }
        },
        {
            "data": {
                "orders": [{"id": order_b, "update_time": 1776840600}],
                "next_page_token": None,
            }
        },
    ]
    client.detail_by_order = {
        order_a: _detail_payload(order_a, "AWAITING_SHIPMENT", [f"{order_a}-L1"]),
        order_b: _detail_payload(order_b, "COMPLETED", [f"{order_b}-L1"]),
    }

    sf = session_factory()
    now = int(time.time())
    try:
        stats = await run_reconciliation(
            session_factory=sf, redis=redis, client=client, now=now
        )
    finally:
        await redis.aclose()

    print(f"  stats={stats}")
    assert stats["orders_scanned"] == 2
    assert stats["orders_fulfilled"] == 1  # 只有 SHIP 那单
    assert stats["orders_failed"] == 0
    assert client.list_call_count.get(SHOP_CIPHER) == 2  # RECON-SHOP 两页
    assert len(client.redeem_calls) == 1
    assert client.redeem_calls[0][0] == order_a

    # last_ts 应该推进到 now - BUFFER (300s)
    redis2 = Redis.from_url(settings.redis_url)
    try:
        ts = await redis2.get(LAST_TS_KEY_TPL.format(shop_id=SHOP_ID))
    finally:
        await redis2.aclose()
    assert ts is not None and int(ts) == now - 300
    print(f"  last_ts advanced to {int(ts)} (now-300)")
    print("  OK")
    print()


async def case_2_singleton_lock() -> None:
    print("=" * 60)
    print("Case 2: 单实例锁 — 同时 acquire_run_lock，第二个立刻跳过")
    print("=" * 60)

    settings = get_settings()
    redis = Redis.from_url(settings.redis_url)
    try:
        async with acquire_run_lock(redis):
            raised = False
            try:
                async with acquire_run_lock(redis):
                    pass
            except ReconciliationAlreadyRunning:
                raised = True
                print("  second acquire raised ReconciliationAlreadyRunning ✓")
            assert raised
    finally:
        await redis.aclose()
    print("  OK")
    print()


async def case_3_api_fail_no_advance() -> None:
    print("=" * 60)
    print("Case 3: get_order_list 抛异常 → last_ts 不推进")
    print("=" * 60)

    await _prepare_fixtures(n_codes=3)
    await _cleanup()

    settings = get_settings()
    redis = Redis.from_url(settings.redis_url)

    client = FakeTTSClient()
    client.list_exc = RuntimeError("simulated TT 504")

    sf = session_factory()
    now = int(time.time())
    try:
        stats = await run_reconciliation(
            session_factory=sf, redis=redis, client=client, now=now
        )
    finally:
        await redis.aclose()

    print(f"  stats={stats}")
    # 只检查 RECON-SHOP 是否 report 了 get_order_list 错误
    recon_errors = [e for e in stats["errors"] if e.get("shop_id") == SHOP_ID]
    assert len(recon_errors) == 1, f"expected 1 error for RECON-SHOP, got {recon_errors}"
    assert recon_errors[0]["stage"] == "get_order_list"

    redis2 = Redis.from_url(settings.redis_url)
    try:
        ts = await redis2.get(LAST_TS_KEY_TPL.format(shop_id=SHOP_ID))
    finally:
        await redis2.aclose()
    assert ts is None, f"expected NO last_ts advance for RECON-SHOP, got {ts!r}"
    print("  last_ts NOT advanced (still unset) ✓")
    print("  OK")
    print()


async def case_4_no_window() -> None:
    print("=" * 60)
    print("Case 4: last_ts 已 >= till → shops_skipped，不调 API")
    print("=" * 60)

    await _prepare_fixtures(n_codes=1)
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url)

    # 手工设置 last_ts 到未来（确保 >= till = now - 300）
    future = int(time.time()) + 1000
    await redis.set(LAST_TS_KEY_TPL.format(shop_id=SHOP_ID), str(future))

    client = FakeTTSClient()
    # 不配 list_pages —— RECON-SHOP 跳过，不应调用 get_order_list（其他 shop 可能会调）

    sf = session_factory()
    try:
        stats = await run_reconciliation(
            session_factory=sf, redis=redis, client=client
        )
    finally:
        await redis.aclose()

    print(f"  stats={stats}")
    assert stats["shops_skipped"] >= 1, "至少 RECON-SHOP 被跳过"
    # RECON-SHOP 不应调用 get_order_list
    assert client.list_call_count.get(SHOP_CIPHER, 0) == 0, (
        f"不应对 RECON-SHOP 调用 get_order_list, got {client.list_call_count}"
    )
    print("  OK")
    print()


async def main() -> None:
    await case_1_full_scan()
    await case_2_singleton_lock()
    await case_3_api_fail_no_advance()
    await case_4_no_window()

    # 最终清理
    await _prepare_fixtures(n_codes=0)
    await _cleanup()
    sf = session_factory()
    async with sf() as s:
        async with s.begin():
            await s.execute(delete(TTSShop).where(TTSShop.shop_id == SHOP_ID))

    print("=" * 60)
    print("全部通过 OK — 对账 Job 分页扫描 + 单实例锁 + 失败不推进 + 空窗跳过 贯通")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
