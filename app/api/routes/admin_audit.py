"""审计日志只读接口。

前端拉时间倒序的最近 200 条；目前不做关键词搜索，
后续如有需要再加 GIN 索引到 details 字段。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, verify_admin_session
from app.models.audit_log import AdminAuditLog


router = APIRouter(dependencies=[Depends(verify_admin_session)])


class AuditRow(BaseModel):
    id: int
    operator: str
    action: str
    target_type: str | None
    target_id: str | None
    details: dict | None
    created_at: str


class AuditListResponse(BaseModel):
    items: list[AuditRow]
    total: int
    limit: int
    offset: int


@router.get("", response_model=AuditListResponse)
async def list_audit(
    db: AsyncSession = Depends(get_db),
    operator: str | None = Query(default=None),
    action: str | None = Query(default=None),
    target_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> AuditListResponse:
    conds = []
    if operator:
        conds.append(AdminAuditLog.operator == operator)
    if action:
        conds.append(AdminAuditLog.action == action)
    if target_type:
        conds.append(AdminAuditLog.target_type == target_type)

    cnt_q = select(func.count(AdminAuditLog.id))
    if conds:
        cnt_q = cnt_q.where(and_(*conds))
    total = int((await db.execute(cnt_q)).scalar_one())

    stmt = (
        select(AdminAuditLog)
        .order_by(AdminAuditLog.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if conds:
        stmt = stmt.where(and_(*conds))

    rows = (await db.execute(stmt)).scalars().all()
    return AuditListResponse(
        items=[
            AuditRow(
                id=r.id,
                operator=r.operator,
                action=r.action,
                target_type=r.target_type,
                target_id=r.target_id,
                details=r.details,
                created_at=r.created_at.isoformat(),
            )
            for r in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
