"""SKU 映射管理（内部 SKU ↔ TT 商品 SKU）。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import Integer, and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_permission, verify_admin_session
from app.models.sku_mapping import SkuMapping
from app.models.supplier_sku_link import SupplierSkuLink
from app.services.admin_audit import write_audit
from app.services.product_sync import refresh_tts_product_match_status


logger = logging.getLogger(__name__)


router = APIRouter(dependencies=[Depends(verify_admin_session)])


class SkuRow(BaseModel):
    id: int
    internal_sku_id: str
    product_name: str | None
    seller_sku: str | None
    tts_product_id: str | None
    tts_sku_id: str | None
    face_value: int
    safety_stock_threshold: int | None
    list_price_cents: int | None = None
    inventory_locked: bool = False
    inventory_lock_reason: str | None = None
    inventory_locked_at: str | None = None
    api_link_count: int = 0
    has_auto_fulfill: bool = False
    active: bool
    created_at: str
    updated_at: str


class SkuListResponse(BaseModel):
    items: list[SkuRow]
    total: int


class SkuCreate(BaseModel):
    internal_sku_id: str = Field(min_length=1, max_length=64)
    product_name: str | None = Field(default=None, max_length=256)
    seller_sku: str | None = Field(default=None, max_length=128)
    tts_product_id: str | None = Field(default=None, max_length=64)
    tts_sku_id: str | None = Field(default=None, max_length=64)
    face_value: int = Field(..., ge=0)
    safety_stock_threshold: int | None = Field(default=None, ge=0, le=100000)
    list_price_cents: int | None = Field(default=None, ge=0)
    inventory_locked: bool | None = None
    active: bool = True


class SkuUpdate(BaseModel):
    internal_sku_id: str | None = Field(default=None, min_length=1, max_length=64)
    product_name: str | None = Field(default=None, max_length=256)
    seller_sku: str | None = Field(default=None, max_length=128)
    tts_product_id: str | None = Field(default=None, max_length=64)
    tts_sku_id: str | None = Field(default=None, max_length=64)
    face_value: int | None = Field(default=None, ge=0)
    safety_stock_threshold: int | None = Field(default=None, ge=0, le=100000)
    list_price_cents: int | None = Field(default=None, ge=0)
    inventory_locked: bool | None = None
    active: bool | None = None


def _to_row(
    s: SkuMapping,
    *,
    api_link_count: int = 0,
    has_auto_fulfill: bool = False,
) -> SkuRow:
    return SkuRow(
        id=s.id,
        internal_sku_id=s.internal_sku_id,
        product_name=s.product_name,
        seller_sku=s.seller_sku,
        tts_product_id=s.tts_product_id,
        tts_sku_id=s.tts_sku_id,
        face_value=s.face_value,
        safety_stock_threshold=s.safety_stock_threshold,
        list_price_cents=s.list_price_cents,
        inventory_locked=s.inventory_locked,
        inventory_lock_reason=s.inventory_lock_reason,
        inventory_locked_at=(
            s.inventory_locked_at.isoformat() if s.inventory_locked_at else None
        ),
        api_link_count=api_link_count,
        has_auto_fulfill=has_auto_fulfill,
        active=s.active,
        created_at=s.created_at.isoformat(),
        updated_at=s.updated_at.isoformat(),
    )


async def _find_existing_tts_identity(
    db: AsyncSession,
    *,
    seller_sku: str | None,
    tts_product_id: str | None,
    tts_sku_id: str | None,
    exclude_id: int | None = None,
) -> SkuMapping | None:
    if not (tts_product_id and tts_sku_id):
        return None

    stmt = select(SkuMapping).where(
        SkuMapping.tts_product_id == tts_product_id,
        SkuMapping.tts_sku_id == tts_sku_id,
    )
    if exclude_id is not None:
        stmt = stmt.where(SkuMapping.id != exclude_id)
    return (await db.execute(stmt.limit(1))).scalar_one_or_none()


def _tts_identity_conflict_detail(existing: SkuMapping | None = None) -> str:
    if existing is None:
        return "SKU 映射和已有记录冲突，请检查 internal_sku_id 或 TT 商品字段是否重复。"

    identity = f"{existing.internal_sku_id} / 面额 {existing.face_value}"
    if existing.active:
        return (
            f"这个 TT 商品已经匹配到 SKU「{identity}」。"
            "请在 SKU 映射列表中编辑现有记录，不要重复创建。"
        )
    return (
        f"这个 TT 商品已有一条停用的 SKU 映射「{identity}」。"
        "请在 SKU 映射列表中启用或编辑现有记录，不要重复创建。"
    )


@router.get(
    "",
    response_model=SkuListResponse,
    dependencies=[Depends(require_permission("skus:read"))],
)
async def list_skus(
    db: AsyncSession = Depends(get_db),
    active: bool | None = Query(default=None),
    q: str | None = Query(
        default=None,
        description="按 internal_sku_id / seller_sku / TT ID 模糊搜",
    ),
) -> SkuListResponse:
    link_stats = (
        select(
            SupplierSkuLink.sku_mapping_id,
            func.count(SupplierSkuLink.id).label("api_link_count"),
            func.max(func.cast(SupplierSkuLink.auto_fulfill, Integer)).label(
                "has_auto_fulfill_int"
            ),
        )
        .group_by(SupplierSkuLink.sku_mapping_id)
        .subquery()
    )
    stmt = (
        select(
            SkuMapping,
            func.coalesce(link_stats.c.api_link_count, 0).label("api_link_count"),
            func.coalesce(link_stats.c.has_auto_fulfill_int, 0).label(
                "has_auto_fulfill_int"
            ),
        )
        .outerjoin(link_stats, link_stats.c.sku_mapping_id == SkuMapping.id)
        .order_by(SkuMapping.internal_sku_id, SkuMapping.face_value)
    )
    conds = []
    if active is not None:
        conds.append(SkuMapping.active.is_(active))
    if q:
        like = f"%{q.strip()}%"
        conds.append(
            or_(
                SkuMapping.internal_sku_id.ilike(like),
                SkuMapping.seller_sku.ilike(like),
                SkuMapping.tts_product_id.ilike(like),
                SkuMapping.tts_sku_id.ilike(like),
            )
        )
    if conds:
        stmt = stmt.where(and_(*conds))

    rows = (await db.execute(stmt)).all()
    return SkuListResponse(
        items=[
            _to_row(
                r.SkuMapping,
                api_link_count=int(r.api_link_count or 0),
                has_auto_fulfill=bool(int(r.has_auto_fulfill_int or 0)),
            )
            for r in rows
        ],
        total=len(rows),
    )


@router.post(
    "",
    response_model=SkuRow,
    status_code=201,
    dependencies=[Depends(require_permission("skus:write"))],
)
async def create_sku(
    request: Request,
    body: SkuCreate,
    db: AsyncSession = Depends(get_db),
    operator: str = Depends(verify_admin_session),
) -> SkuRow:
    existing = await _find_existing_tts_identity(
        db,
        seller_sku=body.seller_sku,
        tts_product_id=body.tts_product_id,
        tts_sku_id=body.tts_sku_id,
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=_tts_identity_conflict_detail(existing),
        )

    sku = SkuMapping(**body.model_dump())
    db.add(sku)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        existing = await _find_existing_tts_identity(
            db,
            seller_sku=body.seller_sku,
            tts_product_id=body.tts_product_id,
            tts_sku_id=body.tts_sku_id,
        )
        raise HTTPException(
            status_code=409,
            detail=_tts_identity_conflict_detail(existing),
        )

    await write_audit(
        db,
        operator=operator,
        action="sku_create",
        request=request,
        target_type="sku",
        target_id=sku.id,
        extra=body.model_dump(),
        commit=False,
    )
    await refresh_tts_product_match_status(db)
    await db.commit()
    await db.refresh(sku)
    return _to_row(sku)


@router.patch(
    "/{sku_id}",
    response_model=SkuRow,
    dependencies=[Depends(require_permission("skus:write"))],
)
async def update_sku(
    request: Request,
    sku_id: int = Path(..., ge=1),
    body: SkuUpdate = SkuUpdate(),
    db: AsyncSession = Depends(get_db),
    operator: str = Depends(verify_admin_session),
) -> SkuRow:
    sku = (
        await db.execute(select(SkuMapping).where(SkuMapping.id == sku_id))
    ).scalar_one_or_none()
    if not sku:
        raise HTTPException(status_code=404, detail="sku not found")

    patch = body.model_dump(exclude_none=True)
    if not patch:
        return _to_row(sku)

    if patch.get("inventory_locked") is False:
        patch.setdefault("inventory_lock_reason", None)
        patch.setdefault("inventory_locked_at", None)

    proposed_seller_sku = patch.get("seller_sku", sku.seller_sku)
    proposed_tts_product_id = patch.get("tts_product_id", sku.tts_product_id)
    proposed_tts_sku_id = patch.get("tts_sku_id", sku.tts_sku_id)

    existing = await _find_existing_tts_identity(
        db,
        seller_sku=proposed_seller_sku,
        tts_product_id=proposed_tts_product_id,
        tts_sku_id=proposed_tts_sku_id,
        exclude_id=sku_id,
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=_tts_identity_conflict_detail(existing),
        )

    for k, v in patch.items():
        setattr(sku, k, v)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        existing = await _find_existing_tts_identity(
            db,
            seller_sku=proposed_seller_sku,
            tts_product_id=proposed_tts_product_id,
            tts_sku_id=proposed_tts_sku_id,
            exclude_id=sku_id,
        )
        raise HTTPException(
            status_code=409,
            detail=_tts_identity_conflict_detail(existing),
        )

    await write_audit(
        db,
        operator=operator,
        action="sku_update",
        request=request,
        target_type="sku",
        target_id=sku_id,
        extra={"changes": patch},
        commit=False,
    )
    await refresh_tts_product_match_status(db)
    await db.commit()
    await db.refresh(sku)
    return _to_row(sku)


@router.delete(
    "/{sku_id}",
    dependencies=[Depends(require_permission("skus:write"))],
)
async def delete_sku(
    request: Request,
    sku_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
    operator: str = Depends(verify_admin_session),
) -> dict[str, bool]:
    """软删 = active=False。"""
    sku = (
        await db.execute(select(SkuMapping).where(SkuMapping.id == sku_id))
    ).scalar_one_or_none()
    if not sku:
        raise HTTPException(status_code=404, detail="sku not found")

    await db.execute(
        update(SkuMapping).where(SkuMapping.id == sku_id).values(active=False)
    )
    await write_audit(
        db,
        operator=operator,
        action="sku_delete",
        request=request,
        target_type="sku",
        target_id=sku_id,
        extra={"internal_sku_id": sku.internal_sku_id},
        commit=False,
    )
    await refresh_tts_product_match_status(db)
    await db.commit()
    return {"ok": True}
