"""TT Access Token 管理器。

职责：
- 读/写 `tts_tokens` 表（带 Fernet 加密）
- 当 access_token 即将过期时，用 Redis 分布式锁防止多实例并发 refresh
- 对外暴露 `get_access_token()`，上游代码只需要关心"拿个有效 token"

实现要点（见 plan §十 并发安全）：
1. 先读 DB：如果 access_token 还有 > buffer_seconds，直接返回
2. 拿 Redis 锁 `tts:token_refresh:{app_key}`；锁内再 double-check DB
3. double-check 未过期 → 直接用（其他实例已刷）
4. double-check 仍过期 → 调 refresh API，落库
5. 锁拿不到（其他实例在刷）→ 等待后重读 DB

TT 侧返回的 `access_token_expire_in` 是 **Unix 绝对时间戳**（Week 0 PoC 实测确认）。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import httpx
from redis.asyncio import Redis
from redis.exceptions import LockError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.auth.crypto import decrypt, encrypt
from app.config import Settings
from app.models.token import TTSToken


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RefreshedToken:
    access_token: str
    access_token_expire_at: int
    refresh_token: str
    refresh_token_expire_at: int | None
    seller_name: str | None
    open_id: str | None


class TokenManager:
    LOCK_KEY_TMPL = "tts:token_refresh:{app_key}"
    LOCK_TIMEOUT = 30
    LOCK_BLOCKING_TIMEOUT = 10

    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        redis: Redis,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._redis = redis
        self._http = http_client

    async def get_access_token(self) -> str:
        """拿到一个一定可用的 access_token。若即将过期会自动刷新。"""

        token = await self._load_token()
        if token and not self._needs_refresh(token.access_token_expire_at):
            return decrypt(token.access_token_encrypted)

        return await self._refresh_with_lock()

    async def exchange_auth_code(self, auth_code: str) -> RefreshedToken:
        """OAuth 店主授权完成后，用 auth_code 换 access/refresh token 并落库。

        不走锁：首次授权本就是串行触发（店主点一次），不存在并发 refresh；
        即便罕见并发，_save_token 里的 FOR UPDATE + version_id_col 乐观锁也兜底。
        """
        refreshed = await self._call_token_api(
            "authorized_code", auth_code=auth_code
        )
        await self._save_token(refreshed)
        logger.info(
            "oauth exchange ok app_key=%s seller=%s open_id=%s",
            self._settings.tts_app_key,
            refreshed.seller_name,
            refreshed.open_id,
        )
        return refreshed

    async def _load_token(self) -> TTSToken | None:
        async with self._session_factory() as session:
            return await session.get(TTSToken, self._settings.tts_app_key)

    def _needs_refresh(self, expire_at: int) -> bool:
        return time.time() + self._settings.token_refresh_buffer_seconds >= expire_at

    async def _refresh_with_lock(self) -> str:
        lock_key = self.LOCK_KEY_TMPL.format(app_key=self._settings.tts_app_key)
        lock = self._redis.lock(
            lock_key, timeout=self.LOCK_TIMEOUT, blocking_timeout=self.LOCK_BLOCKING_TIMEOUT
        )
        acquired = await lock.acquire(blocking=True)
        if not acquired:
            logger.warning(
                "token refresh lock busy, waiting for peer to finish app_key=%s",
                self._settings.tts_app_key,
            )
            await asyncio.sleep(1.0)
            token = await self._load_token()
            if token and not self._needs_refresh(token.access_token_expire_at):
                return decrypt(token.access_token_encrypted)
            raise RuntimeError("token refresh timeout, peer did not finish in time")

        try:
            token = await self._load_token()
            if token and not self._needs_refresh(token.access_token_expire_at):
                return decrypt(token.access_token_encrypted)

            refresh_token_plain = (
                decrypt(token.refresh_token_encrypted)
                if token
                else self._settings.tts_refresh_token
            )
            refreshed = await self._call_token_api(
                "refresh_token", refresh_token=refresh_token_plain
            )
            await self._save_token(refreshed)
            return refreshed.access_token
        finally:
            try:
                await lock.release()
            except LockError:
                logger.warning("lock already released or expired: %s", lock_key)

    async def _call_token_api(
        self, grant_type: str, **extra: str
    ) -> RefreshedToken:
        """统一封装 TT auth 服务两种 grant_type：
        - refresh_token → /api/v2/token/refresh （需 refresh_token=...）
        - authorized_code → /api/v2/token/get    （需 auth_code=...）
        两个接口的成功响应结构一致。
        """
        if grant_type == "refresh_token":
            path = "/api/v2/token/refresh"
        elif grant_type == "authorized_code":
            path = "/api/v2/token/get"
        else:  # pragma: no cover
            raise ValueError(f"unsupported grant_type: {grant_type}")

        url = f"{self._settings.tts_auth_base}{path}"
        params: dict[str, str] = {
            "app_key": self._settings.tts_app_key,
            "app_secret": self._settings.tts_app_secret,
            "grant_type": grant_type,
            **{k: v for k, v in extra.items() if v is not None},
        }
        client = self._http or httpx.AsyncClient(
            proxy=self._settings.tts_http_proxy or None,
            trust_env=False,
            timeout=30,
        )
        owns_client = self._http is None
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            payload = resp.json()
        finally:
            if owns_client:
                await client.aclose()

        if payload.get("code") != 0:
            raise RuntimeError(
                f"TT token api failed: grant_type={grant_type} "
                f"code={payload.get('code')} "
                f"message={payload.get('message')!r}"
            )

        data = payload["data"]
        return RefreshedToken(
            access_token=data["access_token"],
            access_token_expire_at=int(data["access_token_expire_in"]),
            refresh_token=data["refresh_token"],
            refresh_token_expire_at=(
                int(data["refresh_token_expire_in"])
                if data.get("refresh_token_expire_in") is not None
                else None
            ),
            seller_name=data.get("seller_name"),
            open_id=data.get("open_id"),
        )

    async def _save_token(self, refreshed: RefreshedToken) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                stmt = select(TTSToken).where(
                    TTSToken.app_key == self._settings.tts_app_key
                ).with_for_update()
                existing = (await session.execute(stmt)).scalar_one_or_none()

                if existing is None:
                    session.add(
                        TTSToken(
                            app_key=self._settings.tts_app_key,
                            access_token_encrypted=encrypt(refreshed.access_token),
                            access_token_expire_at=refreshed.access_token_expire_at,
                            refresh_token_encrypted=encrypt(refreshed.refresh_token),
                            refresh_token_expire_at=refreshed.refresh_token_expire_at,
                            seller_name=refreshed.seller_name,
                            open_id=refreshed.open_id,
                            version=0,
                        )
                    )
                else:
                    existing.access_token_encrypted = encrypt(refreshed.access_token)
                    existing.access_token_expire_at = refreshed.access_token_expire_at
                    existing.refresh_token_encrypted = encrypt(refreshed.refresh_token)
                    existing.refresh_token_expire_at = refreshed.refresh_token_expire_at
                    if refreshed.seller_name:
                        existing.seller_name = refreshed.seller_name
                    if refreshed.open_id:
                        existing.open_id = refreshed.open_id

        logger.info(
            "token refreshed app_key=%s expire_at=%s",
            self._settings.tts_app_key,
            refreshed.access_token_expire_at,
        )
