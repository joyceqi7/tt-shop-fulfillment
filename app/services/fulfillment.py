"""发货主编排。

**重要设计：DB 事务和网络 IO 严格分离。**

反面教材：把 lock_code + redeem_info_callback + mark_used 放同一个事务里。
redeem 是 HTTP 调用，可能秒级响应也可能超时，期间：
- 行级 PG 锁一直持有，阻塞其他 worker
- DB 连接池被占满
- 失败回滚会把 lock_code 的记录也一并回滚，release 逻辑根本没写进库

所以本模块内部所有事务边界都是"DB-only 事务"，每个事务尽快 commit。

入口：`fulfill_order_once(session_factory, client, shop, order_id)`
外层只负责 Redis `order_lock` 防并发，不再传 session。

事务划分：
- T1 拉订单详情：内联 commit fulfillment_log 的一行
- T2 upsert tts_orders / tts_order_lines
- T3 SHIP 的取码（每条 pending line 一次 lock_code 或 fail），一次 commit
- （网络）调 redeem_info_callback
- T4 成功：mark_used + line.status=shipped + 记 log
     失败：release + 清 line.redeemed_code_id + 记 error log，然后 raise

CANCEL / RETURN 分支各自一个事务。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.crypto import decrypt
from app.models.code import Code
from app.models.fulfillment_log import FulfillmentLog
from app.models.order import TTSOrder, TTSOrderLine
from app.models.shop import TTSShop
from app.models.sku_mapping import SkuMapping
from app.models.supplier import Supplier
from app.models.supplier_order import SupplierOrder
from app.models.supplier_sku_link import SupplierSkuLink
from app.services import code_inventory, procurement
from app.services.supplier_adapters.registry import get_adapter


logger = logging.getLogger(__name__)


class TTSClientLike(Protocol):
    async def get_order_detail(
        self, shop_cipher: str, order_ids: list[str]
    ) -> dict[str, Any]: ...

    async def redeem_info_callback(
        self,
        shop_cipher: str,
        order_id: str,
        order_info_list: list[dict[str, Any]],
    ) -> dict[str, Any]: ...


SHIP_STATUSES = {"AWAITING_SHIPMENT", "PARTIALLY_SHIPPING"}
CANCEL_STATUSES = {"CANCELLED"}
RETURN_STATUSES = {"RETURN"}
TERMINAL_STATUSES = {"DELIVERED", "COMPLETED"}

DETAIL_ENDPOINT = "/order/202507/orders"


async def fulfill_order_once(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    client: TTSClientLike,
    shop: TTSShop,
    order_id: str,
    supplier_delivery_timeout_seconds: int = 300,
) -> dict[str, Any]:
    """一次完整履约。外层必须已持有 order_lock。"""

    from app.services.orders import upsert_order_from_tt

    shop_cipher = shop.shop_cipher
    shop_id = shop.shop_id
    if not shop_cipher:
        raise RuntimeError(f"shop {shop_id} has no shop_cipher")

    # T1: 拉 TT 订单详情
    t0 = time.monotonic()
    try:
        detail = await client.get_order_detail(shop_cipher, [order_id])
    except Exception as e:
        await _write_log(
            session_factory,
            order_id=order_id,
            shop_id=shop_id,
            endpoint=DETAIL_ENDPOINT,
            method="GET",
            request_params={"ids": order_id},
            latency_ms=int((time.monotonic() - t0) * 1000),
            error=str(e),
        )
        raise

    latency_ms = int((time.monotonic() - t0) * 1000)
    await _write_log(
        session_factory,
        order_id=order_id,
        shop_id=shop_id,
        endpoint=DETAIL_ENDPOINT,
        method="GET",
        request_params={"ids": order_id},
        response_body=detail,
        http_status=200,
        api_code=detail.get("code"),
        api_message=detail.get("message"),
        request_id=detail.get("request_id"),
        latency_ms=latency_ms,
    )

    orders = ((detail.get("data") or {}).get("orders")) or []
    if not orders:
        logger.warning("get_order_detail returned empty orders for %s", order_id)
        return {"status": "not_found", "order_id": order_id}

    # T2: upsert 订单 + 行订单
    async with session_factory() as s:
        tts_order, _lines = await upsert_order_from_tt(
            s, order=orders[0], shop_id=shop_id
        )
        order_status = tts_order.status
        await s.commit()

    # T3+T4 或 CANCEL / RETURN
    if order_status in SHIP_STATUSES:
        return await _ship(
            session_factory=session_factory,
            client=client,
            shop_cipher=shop_cipher,
            shop_id=shop_id,
            order_id=order_id,
            supplier_delivery_timeout_seconds=supplier_delivery_timeout_seconds,
        )
    if order_status in CANCEL_STATUSES:
        return await _handle_cancel(session_factory, order_id=order_id)
    if order_status in RETURN_STATUSES:
        return await _handle_return(session_factory, order_id=order_id)
    if order_status in TERMINAL_STATUSES:
        logger.info("order %s already terminal: %s", order_id, order_status)
        return {
            "status": "terminal",
            "order_status": order_status,
            "order_id": order_id,
        }

    logger.info("order %s status %s → no action", order_id, order_status)
    return {"status": "ignored", "order_status": order_status, "order_id": order_id}


async def _ship(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    client: TTSClientLike,
    shop_cipher: str,
    shop_id: str,
    order_id: str,
    supplier_delivery_timeout_seconds: int = 300,
) -> dict[str, Any]:
    """T3: 原子取码 → T_net: 调 redeem → T4: 落账。"""

    # T3: 取码
    async with session_factory() as s:
        pending = list(
            (
                await s.execute(
                    select(TTSOrderLine).where(
                        TTSOrderLine.order_id == order_id,
                        TTSOrderLine.status == "pending",
                    )
                )
            ).scalars()
        )
        if not pending:
            return {
                "status": "already_shipped",
                "order_id": order_id,
                "shipped_count": 0,
            }

        pairs: list[tuple[str, int, str]] = []  # (line_id, code_id, plaintext)
        skipped: list[dict[str, str]] = []
        deferred_realtime: list[tuple[TTSOrderLine, SkuMapping, SupplierSkuLink]] = []

        for line in pending:
            # 复用上一次 redeem 失败留下的 locked code
            if line.redeemed_code_id:
                existing = await s.get(Code, line.redeemed_code_id)
                if existing is not None and existing.status == "locked":
                    pairs.append(
                        (
                            line.order_line_id,
                            existing.id,
                            decrypt(existing.code_encrypted),
                        )
                    )
                    continue

            mapping = await find_mapping_for_line(s, line)
            if mapping is None:
                line.status = "failed"
                line.failed_reason = "sku_not_mapped"
                skipped.append(
                    {"order_line_id": line.order_line_id, "reason": "sku_not_mapped"}
                )
                logger.warning(
                    "no sku_mapping for line %s seller_sku=%r tts_sku_id=%r",
                    line.order_line_id,
                    line.seller_sku,
                    line.tts_sku_id,
                )
                continue

            if mapping.inventory_locked:
                line.status = "failed"
                line.failed_reason = "inventory_locked"
                skipped.append(
                    {
                        "order_line_id": line.order_line_id,
                        "reason": "inventory_locked",
                    }
                )
                logger.warning(
                    "inventory_locked for line %s internal_sku=%s reason=%r",
                    line.order_line_id,
                    mapping.internal_sku_id,
                    mapping.inventory_lock_reason,
                )
                continue

            code = await code_inventory.lock_code(
                s,
                sku_id=mapping.internal_sku_id,
                face_value=mapping.face_value,
                order_line_id=line.order_line_id,
            )
            if code is None:
                auto_link = (
                    await s.execute(
                        select(SupplierSkuLink).where(
                            SupplierSkuLink.sku_mapping_id == mapping.id,
                            SupplierSkuLink.auto_fulfill.is_(True),
                        )
                    )
                ).scalar_one_or_none()
                if auto_link is not None:
                    deferred_realtime.append((line, mapping, auto_link))
                    continue
                line.status = "failed"
                line.failed_reason = "no_stock"
                skipped.append(
                    {"order_line_id": line.order_line_id, "reason": "no_stock"}
                )
                logger.warning(
                    "no stock for line %s internal_sku=%s face=%d",
                    line.order_line_id,
                    mapping.internal_sku_id,
                    mapping.face_value,
                )
                continue

            line.redeemed_code_id = code.id
            pairs.append(
                (line.order_line_id, code.id, decrypt(code.code_encrypted))
            )

        await s.commit()

    supplier_order_by_line: dict[str, int] = {}

    for line, mapping, auto_link in deferred_realtime:
        async with session_factory() as s:
            supplier = await s.get(Supplier, auto_link.supplier_id)
        if supplier is None or not supplier.enabled or get_adapter(supplier) is None:
            async with session_factory() as s:
                db_line = await s.get(TTSOrderLine, line.order_line_id)
                if db_line is not None:
                    db_line.status = "failed"
                    db_line.failed_reason = "supplier_invalid"
                    await s.commit()
            skipped.append(
                {
                    "order_line_id": line.order_line_id,
                    "reason": "supplier_invalid",
                }
            )
            continue
        try:
            acquired = await procurement.realtime_for_line(
                session_factory,
                supplier=supplier,
                mapping=mapping,
                link=auto_link,
                tt_order_id=order_id,
                order_line_id=line.order_line_id,
                timeout_seconds=supplier_delivery_timeout_seconds,
            )
        except Exception as e:
            logger.exception(
                "supplier realtime failed line %s: %s", line.order_line_id, e
            )
            async with session_factory() as s:
                db_line = await s.get(TTSOrderLine, line.order_line_id)
                if db_line is not None:
                    db_line.status = "failed"
                    db_line.failed_reason = "supplier_realtime_failed"
                    await s.commit()
            skipped.append(
                {
                    "order_line_id": line.order_line_id,
                    "reason": "supplier_realtime_failed",
                }
            )
            continue

        async with session_factory() as s:
            db_line = await s.get(TTSOrderLine, line.order_line_id)
            if db_line is not None:
                db_line.redeemed_code_id = acquired.code_id
                await s.commit()

        pairs.append(
            (line.order_line_id, acquired.code_id, acquired.plaintext)
        )
        supplier_order_by_line[line.order_line_id] = acquired.supplier_order_id

    if not pairs:
        return {
            "status": "no_shippable",
            "order_id": order_id,
            "shipped_count": 0,
            "skipped": skipped,
        }

    # T_net: 调 TT Redeem Callback (v202601)
    # source_unique_id 用本地 codes.id，TT 侧基于该字段对同一卡密做幂等去重，
    # 即使我们重试发送也不会被 TT 当成两次新发码。
    order_info_list = [
        {
            "order_line_id": oli,
            "redeem_info": {"redeem_type": "CODE", "redeem_data": plain},
            "source_unique_id": str(cid),
        }
        for (oli, cid, plain) in pairs
    ]
    log_req = {
        "order_info_list_count": len(order_info_list),
        "order_line_ids": [oli for (oli, _cid, _p) in pairs],
        "source_unique_ids": [str(cid) for (_o, cid, _p) in pairs],
    }
    redeem_endpoint = "/fulfillment/202601/redeem_info/callback"

    t0 = time.monotonic()
    try:
        resp = await client.redeem_info_callback(
            shop_cipher, order_id, order_info_list
        )
    except Exception as e:
        latency_ms = int((time.monotonic() - t0) * 1000)
        # T4-err: release + 记失败日志
        async with session_factory() as s:
            for oli, code_id, _plain in pairs:
                await code_inventory.release(s, code_id)
                await s.execute(
                    update(TTSOrderLine)
                    .where(TTSOrderLine.order_line_id == oli)
                    .values(redeemed_code_id=None)
                )
            s.add(
                FulfillmentLog(
                    order_id=order_id,
                    shop_id=shop_id,
                    api_endpoint=redeem_endpoint,
                    api_method="POST",
                    request_body=log_req,
                    latency_ms=latency_ms,
                    error=str(e),
                )
            )
            await s.commit()
        raise

    latency_ms = int((time.monotonic() - t0) * 1000)

    # T4-ok: mark_used + shipped + 记成功日志
    async with session_factory() as s:
        now = datetime.now(tz=timezone.utc)
        for oli, code_id, _plain in pairs:
            await code_inventory.mark_used(s, code_id)
            await s.execute(
                update(TTSOrderLine)
                .where(TTSOrderLine.order_line_id == oli)
                .values(status="shipped", redeemed_at=now, failed_reason=None)
            )
            if oli in supplier_order_by_line:
                await s.execute(
                    update(SupplierOrder)
                    .where(SupplierOrder.id == supplier_order_by_line[oli])
                    .values(
                        status="shipped",
                        completed_at=now,
                    )
                )
        await s.execute(
            update(TTSOrder)
            .where(TTSOrder.order_id == order_id)
            .values(status="DELIVERED", updated_at=now)
        )
        s.add(
            FulfillmentLog(
                order_id=order_id,
                shop_id=shop_id,
                api_endpoint=redeem_endpoint,
                api_method="POST",
                request_body=log_req,
                response_body=resp,
                http_status=200,
                api_code=resp.get("code"),
                api_message=resp.get("message"),
                request_id=resp.get("request_id"),
                latency_ms=latency_ms,
            )
        )
        await s.commit()

    return {
        "status": "shipped",
        "order_id": order_id,
        "shipped_count": len(pairs),
        "skipped": skipped,
    }


async def _handle_cancel(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    order_id: str,
) -> dict[str, Any]:
    """CANCEL：locked → release；used → void。"""
    released = 0
    voided = 0
    async with session_factory() as s:
        lines = list(
            (
                await s.execute(
                    select(TTSOrderLine).where(TTSOrderLine.order_id == order_id)
                )
            ).scalars()
        )
        for line in lines:
            if not line.redeemed_code_id:
                line.status = "cancelled"
                continue
            code = await s.get(Code, line.redeemed_code_id)
            if code is None:
                line.status = "cancelled"
                continue
            if code.status == "locked":
                await code_inventory.release(s, code.id)
                line.redeemed_code_id = None
                line.status = "cancelled"
                released += 1
            elif code.status == "used":
                await code_inventory.void(s, code.id)
                line.status = "cancelled"
                voided += 1
            else:
                line.status = "cancelled"
        await s.commit()

    return {
        "status": "cancelled",
        "order_id": order_id,
        "released": released,
        "voided": voided,
    }


async def _handle_return(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    order_id: str,
) -> dict[str, Any]:
    """RETURN：已发码作废。"""
    voided = 0
    async with session_factory() as s:
        lines = list(
            (
                await s.execute(
                    select(TTSOrderLine).where(TTSOrderLine.order_id == order_id)
                )
            ).scalars()
        )
        for line in lines:
            if not line.redeemed_code_id:
                continue
            code = await s.get(Code, line.redeemed_code_id)
            if code is not None and code.status == "used":
                await code_inventory.void(s, code.id)
                line.status = "refunded"
                voided += 1
        await s.commit()

    return {"status": "return", "order_id": order_id, "voided": voided}


async def reset_failed_lines_for_reship(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    order_id: str,
) -> list[str]:
    """管理员重发前，把未发出的 failed 子单恢复为 pending。

    failed 是一次履约尝试的结果；当运营补完库存或 SKU 映射后，重发需要重新进入
    pending 队列。已绑定码的行不动，避免覆盖正在排查的异常状态。
    """
    async with session_factory() as s:
        result = await s.execute(
            update(TTSOrderLine)
            .where(
                TTSOrderLine.order_id == order_id,
                TTSOrderLine.status == "failed",
                or_(
                    TTSOrderLine.redeemed_code_id.is_(None),
                    TTSOrderLine.redeemed_code_id.in_(
                        select(Code.id).where(Code.status == "locked")
                    ),
                ),
            )
            .values(status="pending", failed_reason=None)
            .returning(TTSOrderLine.order_line_id)
        )
        line_ids = [str(v) for v in result.scalars().all()]
        if line_ids:
            await s.commit()
        return line_ids


async def find_mapping_for_line(
    session: AsyncSession, line: TTSOrderLine
) -> SkuMapping | None:
    exact_tts_sku = (
        await session.execute(
            select(SkuMapping)
            .where(
                SkuMapping.active.is_(True),
                SkuMapping.tts_product_id == line.tts_product_id,
                SkuMapping.tts_sku_id == line.tts_sku_id,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if exact_tts_sku is not None:
        return exact_tts_sku

    if line.seller_sku:
        return (
            await session.execute(
                select(SkuMapping)
                .where(
                    SkuMapping.active.is_(True),
                    SkuMapping.seller_sku == line.seller_sku,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
    return None


async def _write_log(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    order_id: str | None,
    shop_id: str | None,
    endpoint: str,
    method: str,
    request_params: dict | None = None,
    request_body: dict | None = None,
    response_body: dict | None = None,
    http_status: int | None = None,
    api_code: int | None = None,
    api_message: str | None = None,
    request_id: str | None = None,
    latency_ms: int | None = None,
    error: str | None = None,
    order_line_id: str | None = None,
) -> None:
    async with session_factory() as s:
        s.add(
            FulfillmentLog(
                order_id=order_id,
                order_line_id=order_line_id,
                shop_id=shop_id,
                api_endpoint=endpoint,
                api_method=method,
                request_params=request_params,
                request_body=request_body,
                response_body=response_body,
                http_status=http_status,
                api_code=api_code,
                api_message=api_message,
                request_id=request_id,
                latency_ms=latency_ms,
                error=error,
            )
        )
        await s.commit()
