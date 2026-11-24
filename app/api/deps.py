"""FastAPI 依赖注入工厂。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

from arq.connections import ArqRedis
from fastapi import Depends, HTTPException, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.session import session_factory
from app.services.admin_session import SESSION_COOKIE_NAME, parse_cookie


__all__ = [
    "assert_permission",
    "get_arq_pool",
    "get_db",
    "get_redis",
    "get_settings_dep",
    "require_permission",
    "verify_admin_session",
]


def get_settings_dep() -> Settings:
    return get_settings()


async def get_db() -> AsyncIterator[AsyncSession]:
    async with session_factory()() as session:
        yield session


def get_arq_pool(request: Request) -> ArqRedis:
    """从 FastAPI app.state 拿 arq pool（在 lifespan 里建好）。"""
    pool = getattr(request.app.state, "arq_pool", None)
    if pool is None:
        raise RuntimeError(
            "arq pool not initialized — 检查 lifespan 是否被正确注册"
        )
    return pool


def get_redis(request: Request) -> Redis:
    """复用 arq pool 的底层 Redis 连接做通用 key/value 操作。

    arq.ArqRedis 继承自 redis.asyncio.Redis，可直接当 Redis client 用。
    """
    return get_arq_pool(request)


def verify_admin_session(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
) -> str:
    """读取并校验 admin cookie，返回登录用户名。

    未登录 / cookie 损坏 / 过期 → 401。
    ADMIN_SESSION_SECRET 未配置 → 503，给运维一个清晰信号。
    """
    if not settings.admin_session_secret:
        raise HTTPException(
            status_code=503,
            detail="admin console not configured (ADMIN_SESSION_SECRET missing)",
        )
    payload = parse_cookie(
        request.cookies.get(SESSION_COOKIE_NAME), settings.admin_session_secret
    )
    if payload is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return str(payload["u"])


def assert_permission(user: object, permission: str) -> None:
    """Compatibility hook for future granular admin permissions.

    The current admin console only has session-level authentication. Routes can
    depend on this helper now, and user/role enforcement can replace this body
    when the user management system is completed.
    """
    return None


def require_permission(permission: str) -> Callable[..., str]:
    """Return a FastAPI dependency that requires an authenticated admin session."""

    def _dependency(username: str = Depends(verify_admin_session)) -> str:
        assert_permission(username, permission)
        return username

    return _dependency
