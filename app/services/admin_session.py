"""管理后台 cookie session + 登录限流。

设计：
- **不引入 itsdangerous / starlette SessionMiddleware**：对方依赖偏重，且 Starlette 默认
  把整个 session payload 塞进 cookie；我们只放 username + 时效，自己签名简单可控。
- Cookie 格式：`<base64url(payload_json)>.<base64url(hmac_sha256(payload_json))>`
  - payload = {"u": username, "iat": <unix>, "exp": <unix>}
  - 签名失败 / 过期 / 解析异常 一律视为未登录，不区分提示，避免给攻击者反馈
- 限流维度：`(ip, username)` 复合 key —— 防止某 IP 用同一 username 暴力破解；
  也防止"撞库"用同 IP 切换 username 时各自独立计数。
- 限流策略：10 分钟窗口内累计 5 次失败 → 锁 15 分钟（仅锁该 ip+username 组合，不影响其它）
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Any

import bcrypt
from redis.asyncio import Redis


logger = logging.getLogger(__name__)


# --- 常量 ---
SESSION_COOKIE_NAME = "tts_admin_session"

LOGIN_FAIL_KEY_TMPL = "admin:login_fail:{key}"
LOGIN_LOCK_KEY_TMPL = "admin:login_lock:{key}"
LOGIN_FAIL_WINDOW_SECONDS = 600  # 10 分钟
LOGIN_FAIL_THRESHOLD = 5
LOGIN_LOCK_DURATION_SECONDS = 900  # 15 分钟


# ============== bcrypt ==============


def hash_password(password: str, *, rounds: int = 12) -> str:
    """生成 bcrypt 哈希。仅供 scripts/gen_admin_hash.py 使用。"""
    salt = bcrypt.gensalt(rounds=rounds)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, hashed: str | None) -> bool:
    """常量时间比对。hashed 为空 / 格式错误一律返回 False。"""
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ============== Cookie 签发 / 校验 ==============


def issue_cookie(username: str, secret: str, *, ttl_seconds: int) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {"u": username, "iat": now, "exp": now + ttl_seconds}
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = _b64url(_hmac(body.encode("utf-8"), secret))
    return f"{body}.{sig}"


def parse_cookie(value: str | None, secret: str) -> dict[str, Any] | None:
    """成功返回 payload，否则返回 None；不抛异常。"""
    if not value or "." not in value:
        return None
    body, sig = value.rsplit(".", 1)
    try:
        expected = _b64url(_hmac(body.encode("utf-8"), secret))
    except Exception:  # noqa: BLE001
        return None
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        raw = _b64url_decode(body)
        payload = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return None
    exp = payload.get("exp")
    if not isinstance(exp, int) or exp < int(time.time()):
        return None
    if not payload.get("u"):
        return None
    return payload


# ============== 登录限流 ==============


def _rate_key(ip: str, username: str) -> str:
    return f"{ip}:{username.lower()}"


async def get_lockout_remaining(redis: Redis, ip: str, username: str) -> int:
    """0 表示未锁，否则返回剩余锁定秒数。"""
    ttl = await redis.ttl(LOGIN_LOCK_KEY_TMPL.format(key=_rate_key(ip, username)))
    return ttl if ttl > 0 else 0


async def record_login_failure(redis: Redis, ip: str, username: str) -> tuple[int, int]:
    """记一次登录失败。

    返回 (current_failures, lockout_seconds)：
    - 未达阈值时 lockout=0
    - 达到阈值时同步设置锁、清空失败计数，lockout 为 LOGIN_LOCK_DURATION_SECONDS
    """
    key = _rate_key(ip, username)
    fail_key = LOGIN_FAIL_KEY_TMPL.format(key=key)
    n = await redis.incr(fail_key)
    if n == 1:
        await redis.expire(fail_key, LOGIN_FAIL_WINDOW_SECONDS)
    if n >= LOGIN_FAIL_THRESHOLD:
        await redis.setex(
            LOGIN_LOCK_KEY_TMPL.format(key=key),
            LOGIN_LOCK_DURATION_SECONDS,
            "1",
        )
        await redis.delete(fail_key)
        return n, LOGIN_LOCK_DURATION_SECONDS
    return n, 0


async def clear_login_failures(redis: Redis, ip: str, username: str) -> None:
    key = _rate_key(ip, username)
    await redis.delete(LOGIN_FAIL_KEY_TMPL.format(key=key))
    await redis.delete(LOGIN_LOCK_KEY_TMPL.format(key=key))


# ============== 内部工具 ==============


def _hmac(body: bytes, secret: str) -> bytes:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    padding = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)
