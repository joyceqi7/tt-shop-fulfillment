"""供应商采购订单（WGCards 实时 + 批量）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_permission, verify_admin_session
from app.models.purchase_order import PurchaseOrder
from app.models.supplier import Supplier
from app.models.supplier_order import SupplierOrder


router = APIRouter(dependencies=[Depends(verify_admin_session)])


class SupplierOrderRow(BaseModel):
    id: int
    supplier_id: int
    supplier_name: str | None
    procurement_mode: str
    status: str
    tt_order_id: str | None
    tt_order_line_id: str | None
    purchase_order_id: int | None
    po_number: str | None
    supplier_order_id: str | None
    service_order: str | None
    sku_id: str
    buy_num: int
    face_value: float | None
    currency: str
    total_amount: float | None
    card_count: int
    error_message: str | None
    elapsed_seconds: float | None
    placed_at: str | None
    card_ready_at: str | None
    completed_at: str | None
    created_at: str


class SupplierOrderListResponse(BaseModel):
    items: list[SupplierOrderRow]
    total: int
    limit: int
    offset: int


class SupplierOrderDetail(SupplierOrderRow):
    card_codes: list[str] | None
    raw_response: str | None


def _to_row(
    so: SupplierOrder,
    supplier_name: str | None,
    po_number: str | None,
) -> SupplierOrderRow:
    return SupplierOrderRow(
        id=so.id,
        supplier_id=so.supplier_id,
        supplier_name=supplier_name,
        procurement_mode=so.procurement_mode,
        status=so.status,
        tt_order_id=so.tt_order_id,
        tt_order_line_id=so.tt_order_line_id,
        purchase_order_id=so.purchase_order_id,
        po_number=po_number,
        supplier_order_id=so.supplier_order_id,
        service_order=so.service_order,
        sku_id=so.sku_id,
        buy_num=so.buy_num,
        face_value=so.face_value,
        currency=so.currency,
        total_amount=so.total_amount,
        card_count=len(so.card_codes or []),
        error_message=so.error_message,
        elapsed_seconds=so.elapsed_seconds,
        placed_at=so.placed_at.isoformat() if so.placed_at else None,
        card_ready_at=so.card_ready_at.isoformat() if so.card_ready_at else None,
        completed_at=so.completed_at.isoformat() if so.completed_at else None,
        created_at=so.created_at.isoformat(),
    )


@router.get(
    "",
    response_model=SupplierOrderListResponse,
    dependencies=[Depends(require_permission("supplier_orders:read"))],
)
async def list_supplier_orders(
    db: AsyncSession = Depends(get_db),
    procurement_mode: str | None = Query(default=None),
    status: str | None = Query(default=None),
    supplier_id: int | None = Query(default=None),
    tt_order_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> SupplierOrderListResponse:
    conds = []
    if procurement_mode:
        conds.append(SupplierOrder.procurement_mode == procurement_mode)
    if status:
        conds.append(SupplierOrder.status == status)
    if supplier_id:
        conds.append(SupplierOrder.supplier_id == supplier_id)
    if tt_order_id:
        conds.append(SupplierOrder.tt_order_id == tt_order_id)

    cnt_q = select(func.count(SupplierOrder.id))
    if conds:
        cnt_q = cnt_q.where(and_(*conds))
    total = int((await db.execute(cnt_q)).scalar_one())

    stmt = (
        select(SupplierOrder, Supplier.name, PurchaseOrder.po_number)
        .outerjoin(Supplier, Supplier.id == SupplierOrder.supplier_id)
        .outerjoin(PurchaseOrder, PurchaseOrder.id == SupplierOrder.purchase_order_id)
        .order_by(SupplierOrder.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if conds:
        stmt = stmt.where(and_(*conds))

    rows = (await db.execute(stmt)).all()
    return SupplierOrderListResponse(
        items=[
            _to_row(r.SupplierOrder, r.name, r.po_number) for r in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{order_id}",
    response_model=SupplierOrderDetail,
    dependencies=[Depends(require_permission("supplier_orders:read"))],
)
async def supplier_order_detail(
    order_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
) -> SupplierOrderDetail:
    row = (
        await db.execute(
            select(SupplierOrder, Supplier.name, PurchaseOrder.po_number)
            .outerjoin(Supplier, Supplier.id == SupplierOrder.supplier_id)
            .outerjoin(PurchaseOrder, PurchaseOrder.id == SupplierOrder.purchase_order_id)
            .where(SupplierOrder.id == order_id)
        )
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="supplier order not found")

    base = _to_row(row.SupplierOrder, row.name, row.po_number)
    return SupplierOrderDetail(
        **base.model_dump(),
        card_codes=row.SupplierOrder.card_codes,
        raw_response=row.SupplierOrder.raw_response,
    )
