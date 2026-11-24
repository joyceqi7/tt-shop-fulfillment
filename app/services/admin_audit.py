"""敏感操作审计的小封装。

提供一个 `write_audit` 协程，所有 admin 路由的写操作（reship / void / unlock /
import / sku/supplier/purchase 改动 / pay）调用一次，失败时只 warn 不 raise，
不能因为审计写挂了把主流程也搞挂。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AdminAuditLog


logger = logging.getLogger(__name__)


def client_ip(request: Request) -> str:
    """优先 XFF；Caddy 代理后 client.host 总是 127.0.0.1。"""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


async def write_audit(
    db: AsyncSession,
    *,
    operator: str,
    action: str,
    request: Request,
    target_type: str | None = None,
    target_id: str | int | None = None,
    extra: dict[str, Any] | None = None,
    commit: bool = True,
) -> None:
    """写一行审计；commit=False 时由调用方控制事务。"""
    try:
        details: dict[str, Any] = {
            "ip": client_ip(request),
            "user_agent": request.headers.get("user-agent", "")[:256],
        }
        if extra:
            details.update(extra)
        db.add(
            AdminAuditLog(
                operator=operator,
                action=action,
                target_type=target_type,
                target_id=str(target_id) if target_id is not None else None,
                details=details,
            )
        )
        if commit:
            await db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("audit write failed action=%s", action, exc_info=True)
