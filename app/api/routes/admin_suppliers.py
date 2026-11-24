"""供货商管理。

- 软删 = enabled=False（保留历史采购单引用）
- 删除时如果有未付清的 PO，禁止 disable，要求先结清
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    assert_permission,
    get_db,
    require_permission,
    verify_admin_session,
)
from app.models.purchase_order import PurchaseOrder
from app.models.supplier import Supplier
from app.services.admin_audit import write_audit


router = APIRouter(dependencies=[Depends(verify_admin_session)])


# ============== Schemas ==============


class SupplierRow(BaseModel):
    id: int
    name: str
    contact_person: str | None
    contact_phone: str | None
    default_payment_terms_days: int
    note: str | None
    enabled: bool
    supplier_type: Literal["manual", "api"]
    api_host: str | None
    api_app_id: str | None
    api_account_id: str | None
    api_currency: str | None
    is_wgcards: bool = False
    po_count: int
    unpaid_count: int
    created_at: str


class SupplierListResponse(BaseModel):
    items: list[SupplierRow]


class SupplierCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    supplier_type: Literal["manual", "api"] = "manual"
    contact_person: str | None = Field(default=None, max_length=64)
    contact_phone: str | None = Field(default=None, max_length=32)
    default_payment_terms_days: int = Field(default=30, ge=0, le=365)
    note: str | None = None
    api_host: str | None = None
    api_app_id: str | None = None
    api_account_id: str | None = None
    api_app_key: str | None = None
    api_currency: str | None = Field(default="USD")


class SupplierUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    supplier_type: Literal["manual", "api"] | None = None
    contact_person: str | None = Field(default=None, max_length=64)
    contact_phone: str | None = Field(default=None, max_length=32)
    default_payment_terms_days: int | None = Field(default=None, ge=0, le=365)
    note: str | None = None
    enabled: bool | None = None
    api_host: str | None = None
    api_app_id: str | None = None
    api_account_id: str | None = None
    api_app_key: str | None = None
    api_currency: str | None = None


# ============== Routes ==============


def _supplier_row(
    s: Supplier,
    *,
    po_count: int = 0,
    unpaid_count: int = 0,
) -> SupplierRow:
    return SupplierRow(
        id=s.id,
        name=s.name,
        contact_person=s.contact_person,
        contact_phone=s.contact_phone,
        default_payment_terms_days=s.default_payment_terms_days,
        note=s.note,
        enabled=s.enabled,
        supplier_type=s.supplier_type,  # type: ignore[arg-type]
        api_host=s.api_host,
        api_app_id=s.api_app_id,
        api_account_id=s.api_account_id,
        api_currency=s.api_currency,
        is_wgcards=s.is_wgcards,
        po_count=po_count,
        unpaid_count=unpaid_count,
        created_at=s.created_at.isoformat(),
    )


def _apply_supplier_type_fields(
    data: dict[str, object], supplier_type: str
) -> dict[str, object]:
    out = dict(data)
    out["supplier_type"] = supplier_type
    if supplier_type == "manual":
        out["adapter_key"] = None
        out["api_host"] = None
        out["api_app_id"] = None
        out["api_account_id"] = None
        out["api_app_key"] = None
        out["api_currency"] = None
    else:
        out.setdefault("adapter_key", "wgcards")
    return out


def _scrub_supplier_secret(data: dict[str, object]) -> dict[str, object]:
    out = dict(data)
    if out.get("api_app_key"):
        out["api_app_key"] = "***"
    return out


def _contains_api_credentials(data: dict[str, object]) -> bool:
    if data.get("supplier_type") == "api":
        return True
    credential_keys = ("api_host", "api_app_id", "api_account_id", "api_app_key")
    if any(data.get(k) for k in credential_keys):
        return True
    return "api_currency" in data and data.get("api_currency") not in (None, "", "USD")


@router.get(
    "",
    response_model=SupplierListResponse,
    dependencies=[Depends(require_permission("suppliers:read"))],
)
async def list_suppliers(
    db: AsyncSession = Depends(get_db),
    enabled: bool | None = Query(default=None),
) -> SupplierListResponse:
    po_stats = (
        select(
            PurchaseOrder.supplier_id,
            func.count(PurchaseOrder.id).label("po_count"),
            func.sum(
                case((PurchaseOrder.payment_status != "paid", 1), else_=0)
            ).label("unpaid_count"),
        )
        .group_by(PurchaseOrder.supplier_id)
        .subquery()
    )
    stmt = (
        select(
            Supplier,
            func.coalesce(po_stats.c.po_count, 0).label("po_count"),
            func.coalesce(po_stats.c.unpaid_count, 0).label("unpaid_count"),
        )
        .outerjoin(po_stats, po_stats.c.supplier_id == Supplier.id)
        .order_by(Supplier.enabled.desc(), Supplier.name)
    )
    if enabled is not None:
        stmt = stmt.where(Supplier.enabled.is_(enabled))

    rows = (await db.execute(stmt)).all()
    return SupplierListResponse(
        items=[
            _supplier_row(
                r.Supplier,
                po_count=int(r.po_count or 0),
                unpaid_count=int(r.unpaid_count or 0),
            )
            for r in rows
        ]
    )


@router.post("", response_model=SupplierRow, status_code=201)
async def create_supplier(
    request: Request,
    body: SupplierCreate,
    db: AsyncSession = Depends(get_db),
    user: str = Depends(require_permission("suppliers:write")),
    operator: str = Depends(verify_admin_session),
) -> SupplierRow:
    raw = body.model_dump()
    if _contains_api_credentials(raw):
        assert_permission(user, "suppliers:credentials")
    supplier_type = raw.pop("supplier_type", "manual")
    if supplier_type == "api" and not raw.get("api_app_key"):
        raise HTTPException(
            status_code=400,
            detail="API supplier requires api_app_key on create",
        )
    fields = _apply_supplier_type_fields(raw, supplier_type)
    s = Supplier(**fields)
    db.add(s)
    try:
        await db.flush()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"supplier name conflict: {e.orig}")

    await write_audit(
        db,
        operator=operator,
        action="supplier_create",
        request=request,
        target_type="supplier",
        target_id=s.id,
        extra=_scrub_supplier_secret(body.model_dump()),
        commit=False,
    )
    await db.commit()
    await db.refresh(s)
    return _supplier_row(s)


@router.patch("/{supplier_id}", response_model=SupplierRow)
async def update_supplier(
    request: Request,
    supplier_id: int = Path(..., ge=1),
    body: SupplierUpdate = SupplierUpdate(),
    db: AsyncSession = Depends(get_db),
    user: str = Depends(require_permission("suppliers:write")),
    operator: str = Depends(verify_admin_session),
) -> SupplierRow:
    s = (
        await db.execute(select(Supplier).where(Supplier.id == supplier_id))
    ).scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="supplier not found")

    patch = body.model_dump(exclude_none=True)
    if _contains_api_credentials(patch):
        assert_permission(user, "suppliers:credentials")
    supplier_type = patch.pop("supplier_type", None)
    if supplier_type is not None:
        patch = _apply_supplier_type_fields(patch, supplier_type)

    # 关闭供货商前先确认没有未付清的 PO
    if patch.get("enabled") is False:
        unpaid = (
            await db.execute(
                select(func.count(PurchaseOrder.id)).where(
                    and_(
                        PurchaseOrder.supplier_id == supplier_id,
                        PurchaseOrder.payment_status != "paid",
                    )
                )
            )
        ).scalar_one()
        if int(unpaid or 0) > 0:
            raise HTTPException(
                status_code=409,
                detail=f"supplier has {unpaid} unpaid purchase order(s); settle before disabling",
            )

    for k, v in patch.items():
        setattr(s, k, v)
    try:
        await db.flush()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"conflict: {e.orig}")

    await write_audit(
        db,
        operator=operator,
        action="supplier_update",
        request=request,
        target_type="supplier",
        target_id=supplier_id,
        extra={"changes": _scrub_supplier_secret(patch)},
        commit=False,
    )
    await db.commit()
    await db.refresh(s)

    # 顺手算一下 stats
    cnt = (
        await db.execute(
            select(
                func.count(PurchaseOrder.id),
                func.sum(
                    case((PurchaseOrder.payment_status != "paid", 1), else_=0)
                ),
            ).where(PurchaseOrder.supplier_id == supplier_id)
        )
    ).first()
    return _supplier_row(
        s,
        po_count=int(cnt[0] or 0) if cnt else 0,
        unpaid_count=int(cnt[1] or 0) if cnt else 0,
    )
