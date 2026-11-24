"""统一采购编排门面。

对外语义：bulk 批量入库、realtime 来单实时（库存不足时）。
内部通过 SupplierAdapter 调用具体供应商 API。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.code import Code
from app.models.purchase_order import PurchaseOrder
from app.models.sku_mapping import SkuMapping
from app.models.supplier import Supplier
from app.models.supplier_order import SupplierOrder
from app.models.supplier_sku_link import SupplierSkuLink
from app.services import code_inventory
from app.services.purchase_helpers import next_po_number
from app.services.supplier_adapters.registry import get_adapter
from app.services.wgcards_client import WGCardsAPIError, WGCardsTimeoutError


logger = logging.getLogger(__name__)

REALTIME_PO_NOTE = "auto-realtime"


@dataclass(slots=True)
class BulkPurchaseResult:
    purchase_order_id: int
    po_number: str
    supplier_order_id: int
    external_order_id: str
    batch_id: str
    cards_received: int
    cards_imported: int


@dataclass(slots=True)
class RealtimeAcquireResult:
    supplier_order_id: int
    purchase_order_id: int
    code_id: int
    plaintext: str


@dataclass(slots=True)
class AutoFulfillContext:
    supplier_id: int
    external_sku_id: str
    external_face_value: float | None


def link_to_fulfill_context(
    mapping: SkuMapping, link: SupplierSkuLink
) -> AutoFulfillContext:
    face = link.external_face_value
    if face is None:
        face = mapping.face_value / 100.0
    return AutoFulfillContext(
        supplier_id=link.supplier_id,
        external_sku_id=link.external_sku_id,
        external_face_value=face,
    )


async def bulk_to_inventory(
    db: AsyncSession,
    *,
    supplier: Supplier,
    operator: str,
    internal_sku_id: str,
    face_value: int,
    external_sku_id: str,
    external_face_value: float | None,
    quantity: int,
    unit_cost: int,
    purchase_date: date,
    note: str | None = None,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
) -> BulkPurchaseResult:
    """API 批量采购：建 PO → 下单取卡 → 导入 free 库存。"""
    adapter = get_adapter(supplier)
    if adapter is None:
        raise ValueError(f"supplier {supplier.id} has no API adapter")

    if quantity < 1 or quantity > 100:
        raise ValueError("quantity must be 1..100")

    po_number = await next_po_number(db)
    payment_due = purchase_date + timedelta(days=supplier.default_payment_terms_days)
    total_cost = quantity * unit_cost
    currency = supplier.api_currency or "USD"

    po = PurchaseOrder(
        po_number=po_number,
        supplier_id=supplier.id,
        sku_id=internal_sku_id,
        face_value=face_value,
        quantity=quantity,
        unit_cost=unit_cost,
        total_cost=total_cost,
        purchase_date=purchase_date,
        payment_due_date=payment_due,
        note=note,
        payment_status="paid",
        amount_paid=total_cost,
        paid_at=datetime.now(tz=timezone.utc),
    )
    db.add(po)
    await db.flush()

    so = SupplierOrder(
        supplier_id=supplier.id,
        procurement_mode="bulk",
        purchase_order_id=po.id,
        tt_order_id=None,
        tt_order_line_id=None,
        service_order=po_number,
        status="pending",
        sku_id=external_sku_id,
        buy_num=quantity,
        face_value=external_face_value,
        currency=currency,
    )
    db.add(so)
    await db.flush()
    await db.commit()

    try:
        acquired = await adapter.place_and_fetch(
            service_order=po_number,
            external_sku_id=external_sku_id,
            buy_num=quantity,
            face_value=external_face_value,
            currency=currency,
            timeout_seconds=timeout_seconds,
            poll_interval=poll_interval,
        )
    except (WGCardsAPIError, WGCardsTimeoutError) as e:
        await db.execute(
            update(SupplierOrder)
            .where(SupplierOrder.id == so.id)
            .values(status="failed", error_message=str(e))
        )
        await db.commit()
        raise

    plaintexts = acquired.plaintexts
    if len(plaintexts) < quantity:
        logger.warning(
            "bulk %s expected %d cards got %d",
            po_number,
            quantity,
            len(plaintexts),
        )

    await db.execute(
        update(SupplierOrder)
        .where(SupplierOrder.id == so.id)
        .values(
            supplier_order_id=acquired.external_order_id,
            status="processing",
            placed_at=datetime.now(tz=timezone.utc),
            card_codes=plaintexts,
            raw_response=acquired.raw_response,
        )
    )

    import_result = await code_inventory.import_codes(
        db,
        sku_id=internal_sku_id,
        face_value=face_value,
        plaintexts=plaintexts,
        operator=operator or "api_bulk",
    )
    if import_result.inserted == 0:
        await db.execute(
            update(SupplierOrder)
            .where(SupplierOrder.id == so.id)
            .values(
                status="failed",
                error_message="no codes imported (all duplicates?)",
            )
        )
        await db.commit()
        raise ValueError("no codes imported")

    now = datetime.now(tz=timezone.utc)
    await db.execute(
        update(PurchaseOrder)
        .where(PurchaseOrder.id == po.id)
        .values(batch_id=import_result.batch_id)
    )
    await db.execute(
        update(Code)
        .where(Code.batch_id == import_result.batch_id)
        .values(purchase_order_id=po.id)
    )
    await db.execute(
        update(SupplierOrder)
        .where(SupplierOrder.id == so.id)
        .values(
            status="imported",
            card_ready_at=now,
            completed_at=now,
            total_amount=float(total_cost) / 100.0,
        )
    )

    return BulkPurchaseResult(
        purchase_order_id=po.id,
        po_number=po_number,
        supplier_order_id=so.id,
        external_order_id=acquired.external_order_id,
        batch_id=import_result.batch_id,
        cards_received=len(plaintexts),
        cards_imported=import_result.inserted,
    )


async def realtime_for_line(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    supplier: Supplier,
    mapping: SkuMapping,
    link: SupplierSkuLink,
    tt_order_id: str,
    order_line_id: str,
    operator: str = "api_realtime",
    timeout_seconds: int = 300,
    poll_interval: int = 5,
) -> RealtimeAcquireResult:
    """库存不足时 API 实时采购一张码，自动建 PO 并锁定到订单行。"""
    ctx = link_to_fulfill_context(mapping, link)

    adapter = get_adapter(supplier)
    if adapter is None:
        raise ValueError(f"supplier {supplier.id} has no API adapter")

    service_order = f"TT-{tt_order_id}-{order_line_id}"
    currency = supplier.api_currency or "USD"
    today = date.today()

    async with session_factory() as db:
        po_number = await next_po_number(db)
        payment_due = today + timedelta(days=supplier.default_payment_terms_days)
        po = PurchaseOrder(
            po_number=po_number,
            supplier_id=supplier.id,
            sku_id=mapping.internal_sku_id,
            face_value=mapping.face_value,
            quantity=1,
            unit_cost=0,
            total_cost=0,
            purchase_date=today,
            payment_due_date=payment_due,
            note=REALTIME_PO_NOTE,
            payment_status="unpaid",
            amount_paid=0,
        )
        db.add(po)
        await db.flush()

        so = SupplierOrder(
            supplier_id=supplier.id,
            procurement_mode="realtime",
            purchase_order_id=po.id,
            tt_order_id=tt_order_id,
            tt_order_line_id=order_line_id,
            service_order=service_order,
            status="pending",
            sku_id=ctx.external_sku_id,
            buy_num=1,
            face_value=ctx.external_face_value,
            currency=currency,
        )
        db.add(so)
        await db.flush()
        po_id = po.id
        so_id = so.id
        await db.commit()

    try:
        acquired = await adapter.place_and_fetch(
            service_order=service_order,
            external_sku_id=ctx.external_sku_id,
            buy_num=1,
            face_value=ctx.external_face_value,
            currency=currency,
            timeout_seconds=timeout_seconds,
            poll_interval=poll_interval,
        )
    except (WGCardsAPIError, WGCardsTimeoutError) as e:
        async with session_factory() as db:
            async with db.begin():
                await db.execute(
                    update(SupplierOrder)
                    .where(SupplierOrder.id == so_id)
                    .values(status="failed", error_message=str(e))
                )
        raise

    if not acquired.plaintexts:
        async with session_factory() as db:
            async with db.begin():
                await db.execute(
                    update(SupplierOrder)
                    .where(SupplierOrder.id == so_id)
                    .values(
                        status="failed",
                        error_message="supplier returned no cards",
                    )
                )
        raise WGCardsAPIError("no cards returned")

    plaintext = acquired.plaintexts[0]
    unit_cost_cents = 0
    if acquired.total_amount is not None:
        unit_cost_cents = int(round(acquired.total_amount * 100))

    async with session_factory() as db:
        async with db.begin():
            if unit_cost_cents > 0:
                await db.execute(
                    update(PurchaseOrder)
                    .where(PurchaseOrder.id == po_id)
                    .values(unit_cost=unit_cost_cents, total_cost=unit_cost_cents)
                )
            await db.execute(
                update(SupplierOrder)
                .where(SupplierOrder.id == so_id)
                .values(
                    supplier_order_id=acquired.external_order_id,
                    status="card_ready",
                    placed_at=datetime.now(tz=timezone.utc),
                    card_ready_at=datetime.now(tz=timezone.utc),
                    card_codes=acquired.plaintexts,
                    raw_response=acquired.raw_response,
                    total_amount=acquired.total_amount,
                )
            )
            code = await code_inventory.import_locked_code(
                db,
                sku_id=mapping.internal_sku_id,
                face_value=mapping.face_value,
                plaintext=plaintext,
                order_line_id=order_line_id,
                operator=operator,
                purchase_order_id=po_id,
            )

    return RealtimeAcquireResult(
        supplier_order_id=so_id,
        purchase_order_id=po_id,
        code_id=code.id,
        plaintext=plaintext,
    )
