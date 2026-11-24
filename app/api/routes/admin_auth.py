"""管理后台登录 / 登出 / 当前用户。

- 登录使用 cookie session（httpOnly + secure + SameSite=Lax）
- 登录失败 IP+username 维度限流，10 分钟内 5 次失败锁 15 分钟
- 所有动作（成功/失败/锁定/登出）落 admin_audit_log

为什么 /me 也要落审计：让"未授权探测"暴露在审计里。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_db,
    get_redis,
    get_settings_dep,
    verify_admin_session,
)
from app.config import Settings
from app.models.audit_log import AdminAuditLog
from app.services.admin_session import (
    SESSION_COOKIE_NAME,
    clear_login_failures,
    get_lockout_remaining,
    issue_cookie,
    record_login_failure,
    verify_password,
)


logger = logging.getLogger(__name__)


router = APIRouter()


# ============== Schemas ==============


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class LoginResponse(BaseModel):
    username: str


class MeResponse(BaseModel):
    username: str


# ============== Helpers ==============


def _client_ip(request: Request) -> str:
    """优先读 X-Forwarded-For（Caddy 反代后），回退 client.host。"""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def _set_session_cookie(
    response: Response, value: str, settings: Settings
) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=value,
        httponly=True,
        secure=settings.is_prod,
        samesite="lax",
        max_age=settings.admin_session_ttl_seconds,
        domain=settings.admin_session_cookie_domain or None,
        path="/",
    )


def _clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        domain=settings.admin_session_cookie_domain or None,
        path="/",
    )


async def _audit(
    db: AsyncSession,
    *,
    operator: str,
    action: str,
    request: Request,
    extra: dict | None = None,
) -> None:
    """落审计日志。失败时只 warn 不 raise，避免影响主流程。"""
    try:
        details: dict = {
            "ip": _client_ip(request),
            "user_agent": request.headers.get("user-agent", "")[:256],
        }
        if extra:
            details.update(extra)
        db.add(
            AdminAuditLog(
                operator=operator,
                action=action,
                target_type=None,
                target_id=None,
                details=details,
            )
        )
        await db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("audit log write failed", exc_info=True)


# ============== Routes ==============


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="登录：返回 cookie session",
)
async def login(
    req: LoginRequest,
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings_dep),
    redis: Redis = Depends(get_redis),
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    if not settings.admin_username or not settings.admin_password_hash or not settings.admin_session_secret:
        raise HTTPException(
            status_code=503,
            detail="admin console not configured (ADMIN_USERNAME / ADMIN_PASSWORD_HASH / ADMIN_SESSION_SECRET)",
        )

    ip = _client_ip(request)
    locked = await get_lockout_remaining(redis, ip, req.username)
    if locked > 0:
        await _audit(
            db,
            operator=req.username,
            action="login_locked",
            request=request,
            extra={"locked_remaining_s": locked},
        )
        raise HTTPException(
            status_code=429,
            detail=f"too many failed attempts, locked for {locked}s",
        )

    # 用户名比对（不暴露"用户名错"还是"密码错"）
    creds_ok = (
        req.username == settings.admin_username
        and verify_password(req.password, settings.admin_password_hash)
    )
    if not creds_ok:
        n, lock_for = await record_login_failure(redis, ip, req.username)
        await _audit(
            db,
            operator=req.username,
            action="login_failed",
            request=request,
            extra={"failure_count": n, "locked_after": lock_for},
        )
        raise HTTPException(status_code=401, detail="invalid credentials")

    await clear_login_failures(redis, ip, req.username)

    cookie = issue_cookie(
        req.username,
        settings.admin_session_secret,
        ttl_seconds=settings.admin_session_ttl_seconds,
    )
    _set_session_cookie(response, cookie, settings)

    await _audit(db, operator=req.username, action="login", request=request)
    logger.info("admin login ok user=%s ip=%s", req.username, ip)
    return LoginResponse(username=req.username)


@router.post("/logout", summary="登出：清 cookie")
async def logout(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings_dep),
    db: AsyncSession = Depends(get_db),
    username: str | None = None,
) -> dict[str, bool]:
    # 无效 cookie 也允许调用（幂等清除）
    from app.services.admin_session import parse_cookie

    if settings.admin_session_secret:
        payload = parse_cookie(
            request.cookies.get(SESSION_COOKIE_NAME), settings.admin_session_secret
        )
        if payload:
            username = str(payload["u"])

    _clear_session_cookie(response, settings)

    if username:
        await _audit(db, operator=username, action="logout", request=request)
        logger.info("admin logout user=%s", username)
    return {"ok": True}


@router.get(
    "/me",
    response_model=MeResponse,
    summary="返回当前登录用户（前端用于路由守卫初始化）",
)
async def me(username: str = Depends(verify_admin_session)) -> MeResponse:
    return MeResponse(username=username)
