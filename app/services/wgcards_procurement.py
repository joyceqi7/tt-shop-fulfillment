"""WGCards 采购编排 — 兼容层，新代码请用 app.services.procurement。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.sku_mapping import SkuMapping
from app.models.supplier import Supplier
from app.models.supplier_sku_link import SupplierSkuLink
from app.services import procurement
from app.services.supplier_adapters.wgcards import build_wgcards_client

__all__ = [
    "BulkPurchaseResult",
    "RealtimeAcquireResult",
    "build_wgcards_client",
    "bulk_purchase_to_inventory",
    "realtime_acquire_for_line",
]


@dataclass(slots=True)
class BulkPurchaseResult:
    purchase_order_id: int
    po_number: str
    supplier_order_id: int
    wgcards_order_id: str
    batch_id: str
    cards_received: int
    cards_imported: int


@dataclass(slots=True)
class RealtimeAcquireResult:
    supplier_order_id: int
    code_id: int
    plaintext: str
    purchase_order_id: int | None = None


async def bulk_purchase_to_inventory(
    db: AsyncSession,
    *,
    supplier: Supplier,
    operator: str,
    internal_sku_id: str,
    face_value: int,
    wgcards_sku_id: str,
    wgcards_face_value: float | None,
    quantity: int,
    unit_cost: int,
    purchase_date: date,
    note: str | None = None,
    timeout_seconds: int = 300,
) -> BulkPurchaseResult:
    r = await procurement.bulk_to_inventory(
        db,
        supplier=supplier,
        operator=operator,
        internal_sku_id=internal_sku_id,
        face_value=face_value,
        external_sku_id=wgcards_sku_id,
        external_face_value=wgcards_face_value,
        quantity=quantity,
        unit_cost=unit_cost,
        purchase_date=purchase_date,
        note=note,
        timeout_seconds=timeout_seconds,
    )
    return BulkPurchaseResult(
        purchase_order_id=r.purchase_order_id,
        po_number=r.po_number,
        supplier_order_id=r.supplier_order_id,
        wgcards_order_id=r.external_order_id,
        batch_id=r.batch_id,
        cards_received=r.cards_received,
        cards_imported=r.cards_imported,
    )


async def realtime_acquire_for_line(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    supplier: Supplier,
    mapping: SkuMapping,
    link: SupplierSkuLink,
    tt_order_id: str,
    order_line_id: str,
    operator: str = "api_realtime",
    timeout_seconds: int = 300,
) -> RealtimeAcquireResult:
    r = await procurement.realtime_for_line(
        session_factory,
        supplier=supplier,
        mapping=mapping,
        link=link,
        tt_order_id=tt_order_id,
        order_line_id=order_line_id,
        operator=operator,
        timeout_seconds=timeout_seconds,
    )
    return RealtimeAcquireResult(
        supplier_order_id=r.supplier_order_id,
        purchase_order_id=r.purchase_order_id,
        code_id=r.code_id,
        plaintext=r.plaintext,
    )
