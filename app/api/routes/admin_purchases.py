"""采购单管理（CRUD + 付款）。

- 创建时如果没填 po_number，自动生成 PO20260427-001 风格
- 创建时根据 supplier.default_payment_terms_days 算 payment_due_date
- 不允许修改已 paid 的 PO（防止误改影响财务对账）
- 删除 = 仅当 batch_id IS NULL 才允许（已导入的 PO 删了会让 codes 成本归属变孤儿）
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, verify_admin_session
from app.models.purchase_order import PO_PAYMENT_STATUSES, PurchaseOrder
from app.models.supplier import Supplier
from app.services.admin_audit import write_audit


router = APIRouter(dependencies=[Depends(verify_admin_session)])


# ============== Schemas ==============


class PurchaseRow(BaseModel):
    id: int
    po_number: str
    supplier_id: int
    supplier_name: str | None
    sku_id: str
    face_value: int
    quantity: int
    unit_cost: int
    total_cost: int
    purchase_date: str
    payment_due_date: str | None
    amount_paid: int
    payment_status: str
    paid_at: str | None
    batch_id: str | None
    note: str | None
    created_at: str


class PurchaseListResponse(BaseModel):
    items: list[PurchaseRow]
    total: int
    limit: int
    offset: int


class PurchaseCreate(BaseModel):
    po_number: str | None = Field(default=None, max_length=64)
    supplier_id: int = Field(..., ge=1)
    sku_id: str = Field(min_length=1, max_length=64)
    face_value: int = Field(..., ge=0)
    quantity: int = Field(..., ge=1)
    unit_cost: int = Field(..., ge=0, description="单价（美分）")
    purchase_date: date
    payment_due_date: date | None = None
    note: str | None = None


class PurchaseUpdate(BaseModel):
    quantity: int | None = Field(default=None, ge=1)
    unit_cost: int | None = Field(default=None, ge=0)
    purchase_date: date | None = None
    payment_due_date: date | None = None
    note: str | None = None


class PayRequest(BaseModel):
    amount: int = Field(..., ge=1, description="本次付款金额（美分）")
    note: str | None = None


# ============== Helpers ==============


async def _next_po_number(db: AsyncSession) -> str:
    today = date.today().strftime("%Y%m%d")
    prefix = f"PO{today}-"
    n = (
        await db.execute(
            select(func.count(PurchaseOrder.id)).where(
                PurchaseOrder.po_number.like(f"{prefix}%")
            )
        )
    ).scalar_one()
    return f"{prefix}{(int(n or 0) + 1):03d}"


def _to_row(po: PurchaseOrder, supplier_name: str | None) -> PurchaseRow:
    return PurchaseRow(
        id=po.id,
        po_number=po.po_number,
        supplier_id=po.supplier_id,
        supplier_name=supplier_name,
        sku_id=po.sku_id,
        face_value=po.face_value,
        quantity=po.quantity,
        unit_cost=po.unit_cost,
        total_cost=po.total_cost,
        purchase_date=po.purchase_date.isoformat(),
        payment_due_date=po.payment_due_date.isoformat() if po.payment_due_date else None,
        amount_paid=po.amount_paid,
        payment_status=po.payment_status,
        paid_at=po.paid_at.isoformat() if po.paid_at else None,
        batch_id=po.batch_id,
        note=po.note,
        created_at=po.created_at.isoformat(),
    )


# ============== Routes ==============


@router.get("", response_model=PurchaseListResponse)
async def list_purchases(
    db: AsyncSession = Depends(get_db),
    payment_status: str | None = Query(default=None),
    supplier_id: int | None = Query(default=None),
    sku_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PurchaseListResponse:
    conds = []
    if payment_status:
        if payment_status not in PO_PAYMENT_STATUSES:
            raise HTTPException(status_code=400, detail="invalid payment_status")
        conds.append(PurchaseOrder.payment_status == payment_status)
    if supplier_id:
        conds.append(PurchaseOrder.supplier_id == supplier_id)
    if sku_id:
        conds.append(PurchaseOrder.sku_id == sku_id)

    cnt_q = select(func.count(PurchaseOrder.id))
    if conds:
        cnt_q = cnt_q.where(and_(*conds))
    total = int((await db.execute(cnt_q)).scalar_one())

    stmt = (
        select(PurchaseOrder, Supplier.name)
        .outerjoin(Supplier, Supplier.id == PurchaseOrder.supplier_id)
        .order_by(PurchaseOrder.purchase_date.desc(), PurchaseOrder.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if conds:
        stmt = stmt.where(and_(*conds))

    rows = (await db.execute(stmt)).all()
    return PurchaseListResponse(
        items=[_to_row(r.PurchaseOrder, r.name) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{po_id}", response_model=PurchaseRow)
async def purchase_detail(
    po_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
) -> PurchaseRow:
    row = (
        await db.execute(
            select(PurchaseOrder, Supplier.name)
            .outerjoin(Supplier, Supplier.id == PurchaseOrder.supplier_id)
            .where(PurchaseOrder.id == po_id)
        )
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="purchase order not found")
    return _to_row(row.PurchaseOrder, row.name)


@router.post("", response_model=PurchaseRow, status_code=201)
async def create_purchase(
    request: Request,
    body: PurchaseCreate,
    db: AsyncSession = Depends(get_db),
    operator: str = Depends(verify_admin_session),
) -> PurchaseRow:
    supplier = (
        await db.execute(select(Supplier).where(Supplier.id == body.supplier_id))
    ).scalar_one_or_none()
    if not supplier:
        raise HTTPException(status_code=404, detail="supplier not found")
    if not supplier.enabled:
        raise HTTPException(status_code=409, detail="supplier is disabled")

    po_number = body.po_number or await _next_po_number(db)
    payment_due = body.payment_due_date or (
        body.purchase_date + timedelta(days=supplier.default_payment_terms_days)
    )
    total_cost = body.quantity * body.unit_cost

    po = PurchaseOrder(
        po_number=po_number,
        supplier_id=body.supplier_id,
        sku_id=body.sku_id,
        face_value=body.face_value,
        quantity=body.quantity,
        unit_cost=body.unit_cost,
        total_cost=total_cost,
        purchase_date=body.purchase_date,
        payment_due_date=payment_due,
        note=body.note,
    )
    db.add(po)
    try:
        await db.flush()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail=f"po_number conflict: {e.orig}"
        )

    await write_audit(
        db,
        operator=operator,
        action="purchase_create",
        request=request,
        target_type="purchase",
        target_id=po.id,
        extra={
            "po_number": po_number,
            "supplier_id": supplier.id,
            "supplier_name": supplier.name,
            "sku_id": body.sku_id,
            "quantity": body.quantity,
            "unit_cost": body.unit_cost,
            "total_cost": total_cost,
        },
        commit=False,
    )
    await db.commit()
    await db.refresh(po)
    return _to_row(po, supplier.name)


@router.patch("/{po_id}", response_model=PurchaseRow)
async def update_purchase(
    request: Request,
    po_id: int = Path(..., ge=1),
    body: PurchaseUpdate = PurchaseUpdate(),
    db: AsyncSession = Depends(get_db),
    operator: str = Depends(verify_admin_session),
) -> PurchaseRow:
    po = (
        await db.execute(select(PurchaseOrder).where(PurchaseOrder.id == po_id))
    ).scalar_one_or_none()
    if not po:
        raise HTTPException(status_code=404, detail="purchase order not found")
    if po.payment_status == "paid":
        raise HTTPException(
            status_code=409,
            detail="paid purchase order is read-only (avoid finance drift)",
        )

    patch = body.model_dump(exclude_none=True)
    for k, v in patch.items():
        setattr(po, k, v)
    if "quantity" in patch or "unit_cost" in patch:
        po.total_cost = po.quantity * po.unit_cost

    await db.flush()

    await write_audit(
        db,
        operator=operator,
        action="purchase_update",
        request=request,
        target_type="purchase",
        target_id=po_id,
        extra={"changes": patch, "new_total_cost": po.total_cost},
        commit=False,
    )
    await db.commit()
    await db.refresh(po)

    name = (
        await db.execute(select(Supplier.name).where(Supplier.id == po.supplier_id))
    ).scalar_one_or_none()
    return _to_row(po, name)


@router.post("/{po_id}/pay", response_model=PurchaseRow)
async def pay_purchase(
    request: Request,
    body: PayRequest,
    po_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
    operator: str = Depends(verify_admin_session),
) -> PurchaseRow:
    po = (
        await db.execute(select(PurchaseOrder).where(PurchaseOrder.id == po_id))
    ).scalar_one_or_none()
    if not po:
        raise HTTPException(status_code=404, detail="purchase order not found")
    if po.payment_status == "paid":
        raise HTTPException(status_code=409, detail="already fully paid")

    new_paid = po.amount_paid + body.amount
    if new_paid > po.total_cost:
        raise HTTPException(
            status_code=400,
            detail=f"payment exceeds total: {new_paid} > {po.total_cost}",
        )

    if new_paid >= po.total_cost:
        new_status = "paid"
        paid_at = datetime.now(tz=timezone.utc)
    else:
        new_status = "partial"
        paid_at = None

    await db.execute(
        update(PurchaseOrder)
        .where(PurchaseOrder.id == po_id)
        .values(
            amount_paid=new_paid, payment_status=new_status, paid_at=paid_at
        )
    )

    await write_audit(
        db,
        operator=operator,
        action="purchase_pay",
        request=request,
        target_type="purchase",
        target_id=po_id,
        extra={
            "po_number": po.po_number,
            "paid_this_time": body.amount,
            "amount_paid_after": new_paid,
            "total_cost": po.total_cost,
            "new_status": new_status,
            "note": body.note,
        },
        commit=False,
    )
    await db.commit()

    row = (
        await db.execute(
            select(PurchaseOrder, Supplier.name)
            .outerjoin(Supplier, Supplier.id == PurchaseOrder.supplier_id)
            .where(PurchaseOrder.id == po_id)
        )
    ).first()
    assert row is not None
    return _to_row(row.PurchaseOrder, row.name)


@router.delete("/{po_id}")
async def delete_purchase(
    request: Request,
    po_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
    operator: str = Depends(verify_admin_session),
) -> dict[str, bool]:
    po = (
        await db.execute(select(PurchaseOrder).where(PurchaseOrder.id == po_id))
    ).scalar_one_or_none()
    if not po:
        raise HTTPException(status_code=404, detail="purchase order not found")
    if po.batch_id is not None:
        raise HTTPException(
            status_code=409,
            detail="purchase order already has imported codes; cannot delete",
        )
    if po.amount_paid > 0:
        raise HTTPException(
            status_code=409,
            detail="purchase order has payments recorded; cannot delete",
        )

    await db.delete(po)
    await write_audit(
        db,
        operator=operator,
        action="purchase_delete",
        request=request,
        target_type="purchase",
        target_id=po_id,
        extra={"po_number": po.po_number},
        commit=False,
    )
    await db.commit()
    return {"ok": True}
