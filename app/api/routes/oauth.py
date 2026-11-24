"""OAuth 授权流程（给 TT 店主点授权用）。

两个端点：
- GET /oauth/tts/start       生成 state → 302 到 TT 授权入口（店主点一下直达授权页）
- GET /oauth/tts/callback    TT 授权完成回跳：校验 state → 换 token → 同步 shops → 渲染成功页

关键字段：
- service_id = TT Partner Center 里 App 的 Service ID（= App ID）
- state      = 我们生成的随机串，防 CSRF + 防重放
- code/auth_code = TT 带回来的一次性授权码，用来换 token（TT 文档里有时写 code 有时写 auth_code，两个都收）

⚠️ 安全：
- /callback 暴露在公网，任何人可以发请求；靠 state 原子消费 + auth_code 一次性兜底
- 不要给 callback 加鉴权 —— 店主在 TT 那边完成授权后回跳，他没有我们的 admin_token
"""

from __future__ import annotations

import html
import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from redis.asyncio import Redis

from app.api.deps import get_settings_dep
from app.auth.token_manager import TokenManager
from app.config import Settings
from app.db.session import session_factory
from app.services.oauth_state import consume_state, issue_state
from app.services.shop_sync import upsert_shops_from_tt
from app.services.tts_client import TTSClient


logger = logging.getLogger(__name__)


router = APIRouter()


def _get_redis(request: Request) -> Redis:
    """从 arq pool 借一条 Redis 连接（lifespan 里已经建好）。"""
    pool = getattr(request.app.state, "arq_pool", None)
    if pool is None:  # pragma: no cover
        raise RuntimeError("arq pool not initialized")
    return pool


def _build_authorize_url(settings: Settings, state: str) -> str:
    if not settings.tts_service_id:
        raise HTTPException(
            status_code=503,
            detail="TTS_SERVICE_ID not configured — 先把 Partner Center 的 App ID 填进 .env",
        )
    query = urlencode({"service_id": settings.tts_service_id, "state": state})
    return f"{settings.tts_authorize_base.rstrip('/')}/open/authorize?{query}"


@router.get(
    "/tts/start",
    summary="发起 TT OAuth 授权（302 跳转到 TT 授权页）",
)
async def oauth_start(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
) -> RedirectResponse:
    redis = _get_redis(request)
    state = await issue_state(redis, ttl_seconds=settings.oauth_state_ttl_seconds)
    url = _build_authorize_url(settings, state)
    logger.info("oauth start → redirect to TT authorize service_id=%s", settings.tts_service_id)
    return RedirectResponse(url=url, status_code=302)


@router.get(
    "/tts/callback",
    summary="TT 授权完成回跳：验 state → 换 token → 同步 shops",
    response_class=HTMLResponse,
)
async def oauth_callback(
    request: Request,
    code: str | None = Query(default=None, description="TT 某些地区传 code"),
    auth_code: str | None = Query(default=None, description="TT 某些地区传 auth_code"),
    state: str = Query(default="", description="我们在 /start 生成并要求原样带回"),
    settings: Settings = Depends(get_settings_dep),
) -> HTMLResponse:
    redis = _get_redis(request)

    effective_code = code or auth_code
    if not effective_code:
        raise HTTPException(status_code=400, detail="missing auth code parameter")

    if not await consume_state(redis, state):
        logger.warning("oauth callback rejected: invalid/expired state")
        raise HTTPException(
            status_code=400,
            detail="invalid or expired state — 请重新从 /oauth/tts/start 发起授权",
        )

    tm = TokenManager(settings=settings, session_factory=session_factory(), redis=redis)

    try:
        refreshed = await tm.exchange_auth_code(effective_code)
    except Exception as e:  # noqa: BLE001
        logger.exception("oauth exchange failed")
        return HTMLResponse(
            _render_fail_html("Token 交换失败", str(e)),
            status_code=502,
        )

    try:
        client = TTSClient(settings=settings, token_manager=tm)
        shops = await upsert_shops_from_tt(
            client=client, session_factory=session_factory()
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("shop sync failed after token exchange")
        return HTMLResponse(
            _render_fail_html(
                "Token 已换但同步 shop 失败",
                f"token 已落库，{e!s}；请用管理员身份调 /admin/resync_shops 重试",
            ),
            status_code=502,
        )

    logger.info(
        "oauth flow complete seller=%s shops=%d",
        refreshed.seller_name,
        len(shops),
    )

    return HTMLResponse(
        _render_success_html(
            seller_name=refreshed.seller_name or "-",
            shops=[
                {
                    "shop_id": s.shop_id,
                    "shop_name": s.shop_name or "-",
                    "region": s.region or "-",
                }
                for s in shops
            ],
        ),
        status_code=200,
    )


# --- HTML 渲染 ---
# 不引入模板引擎依赖，直接字符串拼；内部页面使用率低、样式要求低

_SUCCESS_TEMPLATE = """\
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>TT Shop 授权成功</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, sans-serif; max-width: 640px;
          margin: 60px auto; padding: 0 20px; color: #222; }}
  h1 {{ color: #0a7; }}
  .card {{ background: #f5f7fa; padding: 20px 24px; border-radius: 8px;
          margin-top: 24px; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
  th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid #ddd; font-size: 14px; }}
  code {{ font-family: ui-monospace, Consolas, monospace; font-size: 13px; }}
</style>
</head>
<body>
  <h1>授权成功</h1>
  <p>店铺已成功授权给 Sailson Shop Fulfillment。现在可以关闭此页面。</p>
  <div class="card">
    <div><strong>Seller:</strong> {seller_name}</div>
    <div><strong>授权店铺数:</strong> {shop_count}</div>
    <table>
      <thead><tr><th>Shop ID</th><th>店铺名</th><th>Region</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
  <p style="margin-top: 32px; color: #888; font-size: 12px;">
    如遇问题请联系对接人。此页由 tts-api.sailson.com 生成。
  </p>
</body>
</html>
"""


_FAIL_TEMPLATE = """\
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>TT Shop 授权失败</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, sans-serif; max-width: 640px;
          margin: 60px auto; padding: 0 20px; color: #222; }}
  h1 {{ color: #c33; }}
  pre {{ background: #faeaea; padding: 14px; border-radius: 6px; overflow: auto; font-size: 13px; }}
</style>
</head>
<body>
  <h1>授权失败</h1>
  <p><strong>{title}</strong></p>
  <pre>{detail}</pre>
  <p>请截图此页面发给对接人，并从 TT 授权入口重新发起。</p>
</body>
</html>
"""


def _render_success_html(*, seller_name: str, shops: list[dict]) -> str:
    rows = "".join(
        "<tr><td><code>{id}</code></td><td>{name}</td><td>{region}</td></tr>".format(
            id=html.escape(s["shop_id"]),
            name=html.escape(s["shop_name"]),
            region=html.escape(s["region"]),
        )
        for s in shops
    ) or "<tr><td colspan=3 style='color:#888'>(暂无，请检查 TT 侧授权状态)</td></tr>"
    return _SUCCESS_TEMPLATE.format(
        seller_name=html.escape(seller_name),
        shop_count=len(shops),
        rows=rows,
    )


def _render_fail_html(title: str, detail: str) -> str:
    return _FAIL_TEMPLATE.format(
        title=html.escape(title),
        detail=html.escape(detail),
    )
