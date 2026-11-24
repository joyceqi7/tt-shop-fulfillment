"""库存：summary / batches / codes / import / void / unlock。

设计要点：
- summary 按 (sku_id, face_value) 聚合 free/locked/used，前端做库存看板
- import 接收 JSON {plaintext_codes: [...], purchase_order_id, expired_at?}
  - 强制必须关联 purchase_order_id（4-23 之后的码必须有成本归属）
  - 复用 services.code_inventory.import_codes，导入完成后回写 batch_id 到 PO
- void/unlock 是单码级运维动作；解锁僵尸锁时不需要订单上下文
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_settings_dep, verify_admin_session
from app.config import Settings
from app.models.audit_log import AdminAuditLog
from app.models.code import Code
from app.models.purchase_order import PurchaseOrder
from app.models.sku_mapping import SkuMapping
from app.services.admin_audit import write_audit
from app.services.code_inventory import import_codes


logger = logging.getLogger(__name__)


router = APIRouter(dependencies=[Depends(verify_admin_session)])


# ============== Schemas ==============


class InventorySkuRow(BaseModel):
    sku_id: str
    product_name: str | None
    face_value: int
    threshold: int
    inventory_tag: Literal["normal", "low", "zero", "stagnant"]
    total: int
    free: int
    locked: int
    used: int
    void: int
    expired: int


class InventorySummaryResponse(BaseModel):
    rows: list[InventorySkuRow]


class InventoryLogRow(BaseModel):
    id: int
    operator: str
    change_type: Literal["新增", "扣减", "回补", "调整"]
    quantity: int
    target_type: str | None
    target_id: str | None
    order_id: str | None
    note: str | None
    created_at: str


class InventoryLogListResponse(BaseModel):
    items: list[InventoryLogRow]
    total: int
    limit: int
    offset: int


class BatchRow(BaseModel):
    batch_id: str
    sku_id: str
    face_value: int
    total: int
    free: int
    used: int
    locked: int
    purchase_order_id: int | None
    po_number: str | None
    earliest_imported: str | None


class BatchListResponse(BaseModel):
    items: list[BatchRow]


class CodeRow(BaseModel):
    id: int
    sku_id: str
    face_value: int
    status: str
    batch_id: str
    purchase_order_id: int | None
    order_line_id: str | None
    locked_at: str | None
    used_at: str | None
    expired_at: str | None
    created_at: str
    operator: str | None


class CodeListResponse(BaseModel):
    items: list[CodeRow]
    total: int
    limit: int
    offset: int


class ImportRequest(BaseModel):
    purchase_order_id: int = Field(..., ge=1)
    plaintext_codes: list[str] = Field(min_length=1, max_length=20000)
    expired_at: datetime | None = None


class ImportResponse(BaseModel):
    batch_id: str
    total: int
    inserted: int
    skipped_duplicate: int
    purchase_order_id: int


class VoidUnlockResponse(BaseModel):
    id: int
    status: Literal["void", "free"]


# ============== Routes ==============


@router.get("/summary", response_model=InventorySummaryResponse)
async def summary(
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> InventorySummaryResponse:
    """按 (sku_id, face_value) 聚合库存。前端按 SKU 横向展开图表。"""
    stmt = (
        select(
            Code.sku_id,
            SkuMapping.product_name,
            SkuMapping.safety_stock_threshold,
            Code.face_value,
            func.count(Code.id).label("total"),
            func.sum(case((Code.status == "free", 1), else_=0)).label("free"),
            func.sum(case((Code.status == "locked", 1), else_=0)).label("locked"),
            func.sum(case((Code.status == "used", 1), else_=0)).label("used"),
            func.sum(case((Code.status == "void", 1), else_=0)).label("void"),
            func.sum(case((Code.status == "expired", 1), else_=0)).label("expired"),
        )
        .outerjoin(
            SkuMapping,
            and_(
                SkuMapping.internal_sku_id == Code.sku_id,
                SkuMapping.face_value == Code.face_value,
            ),
        )
        .group_by(
            Code.sku_id,
            SkuMapping.product_name,
            SkuMapping.safety_stock_threshold,
            Code.face_value,
        )
        .order_by(Code.sku_id, Code.face_value)
    )
    rows = (await db.execute(stmt)).all()
    return InventorySummaryResponse(
        rows=[
            InventorySkuRow(
                sku_id=r.sku_id,
                product_name=r.product_name,
                face_value=r.face_value,
                threshold=int(r.safety_stock_threshold or settings.stock_alert_threshold),
                inventory_tag=(
                    "zero"
                    if int(r.free or 0) == 0
                    else "low"
                    if int(r.free or 0)
                    < int(r.safety_stock_threshold or settings.stock_alert_threshold)
                    else "stagnant"
                    if int(r.used or 0) == 0 and int(r.total or 0) > 0
                    else "normal"
                ),
                total=int(r.total or 0),
                free=int(r.free or 0),
                locked=int(r.locked or 0),
                used=int(r.used or 0),
                void=int(r.void or 0),
                expired=int(r.expired or 0),
            )
            for r in rows
        ]
    )


@router.get("/logs", response_model=InventoryLogListResponse)
async def list_inventory_logs(
    db: AsyncSession = Depends(get_db),
    change_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> InventoryLogListResponse:
    action_map = {"新增": "import", "扣减": "void", "回补": "unlock"}
    action_to_change = {"import": "新增", "void": "扣减", "unlock": "回补"}

    actions = ["import", "void", "unlock"]
    if change_type and change_type in action_map:
        actions = [action_map[change_type]]

    conds = [AdminAuditLog.action.in_(actions)]
    cnt_q = select(func.count(AdminAuditLog.id)).where(*conds)
    total = int((await db.execute(cnt_q)).scalar_one())

    rows = (
        (
            await db.execute(
                select(AdminAuditLog)
                .where(*conds)
                .order_by(AdminAuditLog.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    items: list[InventoryLogRow] = []
    for r in rows:
        details = r.details or {}
        qty = 1
        if r.action == "import":
            qty = int(details.get("inserted") or details.get("total") or 0)
        items.append(
            InventoryLogRow(
                id=r.id,
                operator=r.operator,
                change_type=action_to_change.get(r.action, "调整"),
                quantity=qty,
                target_type=r.target_type,
                target_id=r.target_id,
                order_id=str(details.get("order_id")) if details.get("order_id") else None,
                note=str(details.get("note")) if details.get("note") else None,
                created_at=r.created_at.isoformat(),
            )
        )
    return InventoryLogListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/batches", response_model=BatchListResponse)
async def list_batches(
    db: AsyncSession = Depends(get_db),
    sku_id: str | None = Query(default=None),
) -> BatchListResponse:
    """按 batch_id 聚合 + 关联 PO 信息。"""
    conds = []
    if sku_id:
        conds.append(Code.sku_id == sku_id)

    stmt = (
        select(
            Code.batch_id,
            Code.sku_id,
            Code.face_value,
            Code.purchase_order_id,
            PurchaseOrder.po_number,
            func.count(Code.id).label("total"),
            func.sum(case((Code.status == "free", 1), else_=0)).label("free"),
            func.sum(case((Code.status == "locked", 1), else_=0)).label("locked"),
            func.sum(case((Code.status == "used", 1), else_=0)).label("used"),
            func.min(Code.created_at).label("earliest"),
        )
        .outerjoin(PurchaseOrder, PurchaseOrder.id == Code.purchase_order_id)
        .group_by(
            Code.batch_id,
            Code.sku_id,
            Code.face_value,
            Code.purchase_order_id,
            PurchaseOrder.po_number,
        )
        .order_by(func.min(Code.created_at).desc())
        .limit(200)
    )
    if conds:
        stmt = stmt.where(*conds)

    rows = (await db.execute(stmt)).all()
    return BatchListResponse(
        items=[
            BatchRow(
                batch_id=r.batch_id,
                sku_id=r.sku_id,
                face_value=r.face_value,
                total=int(r.total or 0),
                free=int(r.free or 0),
                locked=int(r.locked or 0),
                used=int(r.used or 0),
                purchase_order_id=r.purchase_order_id,
                po_number=r.po_number,
                earliest_imported=r.earliest.isoformat() if r.earliest else None,
            )
            for r in rows
        ]
    )


@router.get("/codes", response_model=CodeListResponse)
async def list_codes(
    db: AsyncSession = Depends(get_db),
    status: str | None = Query(default=None),
    sku_id: str | None = Query(default=None),
    batch_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> CodeListResponse:
    conds = []
    if status:
        conds.append(Code.status == status)
    if sku_id:
        conds.append(Code.sku_id == sku_id)
    if batch_id:
        conds.append(Code.batch_id == batch_id)

    cnt_q = select(func.count(Code.id))
    if conds:
        cnt_q = cnt_q.where(and_(*conds))
    total = int((await db.execute(cnt_q)).scalar_one())

    q = select(Code).order_by(Code.id.desc()).limit(limit).offset(offset)
    if conds:
        q = q.where(and_(*conds))

    rows = (await db.execute(q)).scalars().all()
    return CodeListResponse(
        items=[
            CodeRow(
                id=c.id,
                sku_id=c.sku_id,
                face_value=c.face_value,
                status=c.status,
                batch_id=c.batch_id,
                purchase_order_id=c.purchase_order_id,
                order_line_id=c.order_line_id,
                locked_at=c.locked_at.isoformat() if c.locked_at else None,
                used_at=c.used_at.isoformat() if c.used_at else None,
                expired_at=c.expired_at.isoformat() if c.expired_at else None,
                created_at=c.created_at.isoformat(),
                operator=c.operator,
            )
            for c in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/import",
    response_model=ImportResponse,
    summary="批量导入卡密（必须关联到 purchase_order）",
)
async def import_route(
    request: Request,
    body: ImportRequest,
    db: AsyncSession = Depends(get_db),
    operator: str = Depends(verify_admin_session),
) -> ImportResponse:
    po = (
        await db.execute(
            select(PurchaseOrder).where(PurchaseOrder.id == body.purchase_order_id)
        )
    ).scalar_one_or_none()
    if not po:
        raise HTTPException(status_code=404, detail="purchase_order not found")
    if po.batch_id is not None:
        raise HTTPException(
            status_code=409,
            detail=f"purchase_order {po.po_number} already has imported codes (batch={po.batch_id})",
        )

    result = await import_codes(
        db,
        sku_id=po.sku_id,
        face_value=po.face_value,
        plaintexts=body.plaintext_codes,
        operator=operator,
        expired_at=body.expired_at,
    )
    if result.inserted == 0:
        await db.rollback()
        raise HTTPException(
            status_code=400,
            detail=f"no codes inserted (total={result.total}, all duplicates)",
        )

    # 把新批次回写到 PO，并把 codes.purchase_order_id 也补上
    await db.execute(
        update(PurchaseOrder)
        .where(PurchaseOrder.id == po.id)
        .values(batch_id=result.batch_id)
    )
    await db.execute(
        update(Code)
        .where(Code.batch_id == result.batch_id)
        .values(purchase_order_id=po.id)
    )

    await write_audit(
        db,
        operator=operator,
        action="import",
        request=request,
        target_type="batch",
        target_id=result.batch_id,
        extra={
            "purchase_order_id": po.id,
            "po_number": po.po_number,
            "sku_id": po.sku_id,
            "face_value": po.face_value,
            "total": result.total,
            "inserted": result.inserted,
            "skipped_duplicate": result.skipped_duplicate,
        },
        commit=False,
    )

    await db.commit()

    return ImportResponse(
        batch_id=result.batch_id,
        total=result.total,
        inserted=result.inserted,
        skipped_duplicate=result.skipped_duplicate,
        purchase_order_id=po.id,
    )


@router.post(
    "/codes/{code_id}/void",
    response_model=VoidUnlockResponse,
    summary="作废一张码（status=void，不会再被取出）",
)
async def void_code(
    request: Request,
    code_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
    operator: str = Depends(verify_admin_session),
) -> VoidUnlockResponse:
    code = (
        await db.execute(select(Code).where(Code.id == code_id))
    ).scalar_one_or_none()
    if not code:
        raise HTTPException(status_code=404, detail="code not found")
    if code.status == "used":
        raise HTTPException(status_code=409, detail="code already used, cannot void")

    await db.execute(update(Code).where(Code.id == code_id).values(status="void"))
    await write_audit(
        db,
        operator=operator,
        action="void",
        request=request,
        target_type="code",
        target_id=code_id,
        extra={
            "from_status": code.status,
            "sku_id": code.sku_id,
            "face_value": code.face_value,
        },
        commit=False,
    )
    await db.commit()
    return VoidUnlockResponse(id=code_id, status="void")


@router.post(
    "/codes/{code_id}/unlock",
    response_model=VoidUnlockResponse,
    summary="把僵尸 locked 码退回 free（应急）",
)
async def unlock_code(
    request: Request,
    code_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
    operator: str = Depends(verify_admin_session),
) -> VoidUnlockResponse:
    code = (
        await db.execute(select(Code).where(Code.id == code_id))
    ).scalar_one_or_none()
    if not code:
        raise HTTPException(status_code=404, detail="code not found")
    if code.status != "locked":
        raise HTTPException(
            status_code=409,
            detail=f"code status is {code.status}, only locked can be unlocked",
        )

    await db.execute(
        update(Code)
        .where(Code.id == code_id)
        .values(status="free", order_line_id=None, locked_at=None)
    )
    await write_audit(
        db,
        operator=operator,
        action="unlock",
        request=request,
        target_type="code",
        target_id=code_id,
        extra={
            "previous_order_line_id": code.order_line_id,
            "sku_id": code.sku_id,
        },
        commit=False,
    )
    await db.commit()
    return VoidUnlockResponse(id=code_id, status="free")
