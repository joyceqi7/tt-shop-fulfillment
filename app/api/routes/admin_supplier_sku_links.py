"""SKU ↔ API 供应商链接 CRUD。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    assert_permission,
    get_db,
    require_permission,
    verify_admin_session,
)
from app.models.sku_mapping import SkuMapping
from app.models.supplier import Supplier
from app.models.supplier_sku_link import SupplierSkuLink
from app.services.admin_audit import write_audit
from app.services.supplier_adapters.wgcards import build_wgcards_client
from app.services.wgcards_client import WGCardsAPIError


router = APIRouter(dependencies=[Depends(verify_admin_session)])


class SupplierSkuLinkRow(BaseModel):
    id: int
    sku_mapping_id: int
    internal_sku_id: str
    face_value: int
    supplier_id: int
    supplier_name: str | None
    supplier_type: str
    external_sku_id: str
    external_face_value: float | None
    auto_fulfill: bool
    supplier_stock: int | None = None
    supplier_stock_error: str | None = None
    created_at: str
    updated_at: str


class SupplierSkuLinkListResponse(BaseModel):
    items: list[SupplierSkuLinkRow]


class SupplierSkuLinkCreate(BaseModel):
    sku_mapping_id: int = Field(..., ge=1)
    supplier_id: int = Field(..., ge=1)
    external_sku_id: str = Field(min_length=1, max_length=64)
    external_face_value: float | None = Field(default=None, ge=0)
    auto_fulfill: bool = False


class SupplierSkuLinkUpdate(BaseModel):
    external_sku_id: str | None = Field(default=None, min_length=1, max_length=64)
    external_face_value: float | None = Field(default=None, ge=0)
    auto_fulfill: bool | None = None


async def _ensure_api_supplier(db: AsyncSession, supplier_id: int) -> Supplier:
    supplier = await db.get(Supplier, supplier_id)
    if supplier is None:
        raise HTTPException(status_code=404, detail="supplier not found")
    if supplier.supplier_type != "api":
        raise HTTPException(status_code=400, detail="supplier is not API type")
    if not supplier.enabled:
        raise HTTPException(status_code=409, detail="supplier is disabled")
    return supplier


async def _ensure_sku(db: AsyncSession, sku_mapping_id: int) -> SkuMapping:
    sku = await db.get(SkuMapping, sku_mapping_id)
    if sku is None:
        raise HTTPException(status_code=404, detail="sku mapping not found")
    return sku


async def _clear_other_auto_fulfill(
    db: AsyncSession, *, sku_mapping_id: int, except_id: int | None = None
) -> None:
    stmt = (
        update(SupplierSkuLink)
        .where(
            SupplierSkuLink.sku_mapping_id == sku_mapping_id,
            SupplierSkuLink.auto_fulfill.is_(True),
        )
        .values(auto_fulfill=False)
    )
    if except_id is not None:
        stmt = stmt.where(SupplierSkuLink.id != except_id)
    await db.execute(stmt)


def _to_row(
    link: SupplierSkuLink,
    *,
    internal_sku_id: str,
    face_value: int,
    supplier_name: str | None,
    supplier_type: str,
    supplier_stock: int | None = None,
    supplier_stock_error: str | None = None,
) -> SupplierSkuLinkRow:
    return SupplierSkuLinkRow(
        id=link.id,
        sku_mapping_id=link.sku_mapping_id,
        internal_sku_id=internal_sku_id,
        face_value=face_value,
        supplier_id=link.supplier_id,
        supplier_name=supplier_name,
        supplier_type=supplier_type,
        external_sku_id=link.external_sku_id,
        external_face_value=link.external_face_value,
        auto_fulfill=link.auto_fulfill,
        supplier_stock=supplier_stock,
        supplier_stock_error=supplier_stock_error,
        created_at=link.created_at.isoformat(),
        updated_at=link.updated_at.isoformat(),
    )


async def _load_supplier_stock(
    rows: list,
) -> dict[int, tuple[int | None, str | None]]:
    by_supplier: dict[int, dict[str, list[int]]] = {}
    for idx, row in enumerate(rows):
        supplier = row.Supplier
        link = row.SupplierSkuLink
        if supplier.supplier_type != "api" or not supplier.enabled:
            continue
        by_supplier.setdefault(supplier.id, {}).setdefault(
            link.external_sku_id, []
        ).append(idx)

    out: dict[int, tuple[int | None, str | None]] = {}
    for supplier_id, sku_map in by_supplier.items():
        supplier = next(r.Supplier for r in rows if r.Supplier.id == supplier_id)
        try:
            client = build_wgcards_client(supplier)
            stocks = await client.get_stock(list(sku_map.keys()))
            stock_by_sku = {s.sku_id: s.number for s in stocks}
            for external_sku_id, indexes in sku_map.items():
                value = stock_by_sku.get(external_sku_id)
                for idx in indexes:
                    out[idx] = (value, None)
        except (WGCardsAPIError, ValueError) as e:
            for indexes in sku_map.values():
                for idx in indexes:
                    out[idx] = (None, str(e))
        except Exception as e:  # noqa: BLE001
            for indexes in sku_map.values():
                for idx in indexes:
                    out[idx] = (None, f"查询库存失败：{e}")
    return out


@router.get(
    "",
    response_model=SupplierSkuLinkListResponse,
    dependencies=[Depends(require_permission("supplier_links:read"))],
)
async def list_supplier_sku_links(
    db: AsyncSession = Depends(get_db),
    sku_mapping_id: int | None = Query(default=None, ge=1),
    supplier_id: int | None = Query(default=None, ge=1),
    for_bulk: bool = Query(default=False, description="仅返回可用于批量入库的链接"),
    include_stock: bool = Query(default=False, description="附带供应商实时库存"),
) -> SupplierSkuLinkListResponse:
    stmt = (
        select(SupplierSkuLink, SkuMapping, Supplier)
        .join(SkuMapping, SkuMapping.id == SupplierSkuLink.sku_mapping_id)
        .join(Supplier, Supplier.id == SupplierSkuLink.supplier_id)
        .order_by(SkuMapping.internal_sku_id, Supplier.name)
    )
    conds = []
    if sku_mapping_id is not None:
        conds.append(SupplierSkuLink.sku_mapping_id == sku_mapping_id)
    if supplier_id is not None:
        conds.append(SupplierSkuLink.supplier_id == supplier_id)
    if for_bulk:
        conds.extend(
            [
                SkuMapping.active.is_(True),
                Supplier.supplier_type == "api",
                Supplier.enabled.is_(True),
            ]
        )
    if conds:
        stmt = stmt.where(and_(*conds))

    rows = (await db.execute(stmt)).all()
    stock_by_index = await _load_supplier_stock(rows) if include_stock else {}
    return SupplierSkuLinkListResponse(
        items=[
            _to_row(
                r.SupplierSkuLink,
                internal_sku_id=r.SkuMapping.internal_sku_id,
                face_value=r.SkuMapping.face_value,
                supplier_name=r.Supplier.name,
                supplier_type=r.Supplier.supplier_type,
                supplier_stock=stock_by_index.get(idx, (None, None))[0],
                supplier_stock_error=stock_by_index.get(idx, (None, None))[1],
            )
            for idx, r in enumerate(rows)
        ]
    )


@router.post(
    "",
    response_model=SupplierSkuLinkRow,
    status_code=201,
)
async def create_supplier_sku_link(
    request: Request,
    body: SupplierSkuLinkCreate,
    db: AsyncSession = Depends(get_db),
    operator: str = Depends(verify_admin_session),
) -> SupplierSkuLinkRow:
    await _ensure_sku(db, body.sku_mapping_id)
    supplier = await _ensure_api_supplier(db, body.supplier_id)

    if body.auto_fulfill:
        assert_permission(operator, "auto_fulfill:write")
        await _clear_other_auto_fulfill(db, sku_mapping_id=body.sku_mapping_id)

    link = SupplierSkuLink(**body.model_dump())
    db.add(link)
    try:
        await db.flush()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"link already exists: {e.orig}")

    sku = await db.get(SkuMapping, body.sku_mapping_id)
    await write_audit(
        db,
        operator=operator,
        action="supplier_sku_link_create",
        request=request,
        target_type="supplier_sku_link",
        target_id=link.id,
        extra=body.model_dump(),
        commit=False,
    )
    await db.commit()
    await db.refresh(link)
    return _to_row(
        link,
        internal_sku_id=sku.internal_sku_id if sku else "",
        face_value=sku.face_value if sku else 0,
        supplier_name=supplier.name,
        supplier_type=supplier.supplier_type,
    )


@router.patch(
    "/{link_id}",
    response_model=SupplierSkuLinkRow,
)
async def update_supplier_sku_link(
    request: Request,
    link_id: int = Path(..., ge=1),
    body: SupplierSkuLinkUpdate = SupplierSkuLinkUpdate(),
    db: AsyncSession = Depends(get_db),
    operator: str = Depends(verify_admin_session),
) -> SupplierSkuLinkRow:
    link = await db.get(SupplierSkuLink, link_id)
    if link is None:
        raise HTTPException(status_code=404, detail="link not found")

    patch = body.model_dump(exclude_none=True)
    if not patch:
        sku = await db.get(SkuMapping, link.sku_mapping_id)
        supplier = await db.get(Supplier, link.supplier_id)
        return _to_row(
            link,
            internal_sku_id=sku.internal_sku_id if sku else "",
            face_value=sku.face_value if sku else 0,
            supplier_name=supplier.name if supplier else None,
            supplier_type=supplier.supplier_type if supplier else "manual",
        )

    if patch.get("auto_fulfill"):
        assert_permission(operator, "auto_fulfill:write")
        await _clear_other_auto_fulfill(
            db, sku_mapping_id=link.sku_mapping_id, except_id=link.id
        )

    for k, v in patch.items():
        setattr(link, k, v)
    await db.flush()

    sku = await db.get(SkuMapping, link.sku_mapping_id)
    supplier = await db.get(Supplier, link.supplier_id)
    await write_audit(
        db,
        operator=operator,
        action="supplier_sku_link_update",
        request=request,
        target_type="supplier_sku_link",
        target_id=link_id,
        extra={"changes": patch},
        commit=False,
    )
    await db.commit()
    await db.refresh(link)
    return _to_row(
        link,
        internal_sku_id=sku.internal_sku_id if sku else "",
        face_value=sku.face_value if sku else 0,
        supplier_name=supplier.name if supplier else None,
        supplier_type=supplier.supplier_type if supplier else "manual",
    )


@router.delete(
    "/{link_id}",
    dependencies=[Depends(require_permission("supplier_links:write"))],
)
async def delete_supplier_sku_link(
    request: Request,
    link_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
    operator: str = Depends(verify_admin_session),
) -> dict[str, bool]:
    link = await db.get(SupplierSkuLink, link_id)
    if link is None:
        raise HTTPException(status_code=404, detail="link not found")

    await db.delete(link)
    await write_audit(
        db,
        operator=operator,
        action="supplier_sku_link_delete",
        request=request,
        target_type="supplier_sku_link",
        target_id=link_id,
        extra={"sku_mapping_id": link.sku_mapping_id, "supplier_id": link.supplier_id},
        commit=False,
    )
    await db.commit()
    return {"ok": True}
