"""健康检查 + 简单运行时信息。负载均衡用 /health/live 和 /health/ready。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_settings_dep
from app.config import Settings


router = APIRouter()


@router.get("/live")
async def live() -> dict[str, str]:
    """进程活着就返回 ok。"""
    return {"status": "ok"}


@router.get("/ready")
async def ready(
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, object]:
    """探测 DB + Redis 都可用。任何一项失败 → 5XX。"""
    db_ok = False
    try:
        await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception as e:  # noqa: BLE001
        return {"status": "fail", "component": "db", "error": str(e)}

    redis = Redis.from_url(settings.redis_url)
    try:
        pong = await redis.ping()
    finally:
        await redis.aclose()

    return {
        "status": "ok" if db_ok and pong else "fail",
        "db": db_ok,
        "redis": bool(pong),
        "env": settings.app_env,
    }
