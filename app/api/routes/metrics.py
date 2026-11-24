"""Prometheus /metrics endpoint。

每次 scrape 调一次 `collect_snapshot`，从 DB + Redis 实时查。

**不加鉴权**——指标里只有聚合数量、没有敏感字段；上线前由网络层（WAF / security group）
限制只允许 Prometheus 所在 IP 访问。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST
from redis.asyncio import Redis

from app.api.deps import get_settings_dep
from app.config import Settings
from app.db.session import session_factory
from app.services.metrics import collect_snapshot, render_prometheus


router = APIRouter()


async def _get_redis(settings: Settings = Depends(get_settings_dep)) -> Redis:
    return Redis.from_url(settings.redis_url)


@router.get(
    "",
    response_class=Response,
    summary="Prometheus metrics",
    include_in_schema=False,
)
async def metrics(request: Request) -> Response:
    settings = request.app.state.settings if hasattr(request.app.state, "settings") else None
    if settings is None:
        from app.config import get_settings as _gs
        settings = _gs()

    redis = Redis.from_url(settings.redis_url)
    try:
        snap = await collect_snapshot(
            session_factory=session_factory(), redis=redis
        )
    finally:
        await redis.aclose()

    body = render_prometheus(snap)
    return Response(content=body, media_type=CONTENT_TYPE_LATEST)
