"""OAuth state 参数管理（CSRF 防护）。

设计：
- /oauth/tts/start 调 issue_state → 生成随机 URL-safe 串，写 Redis（TTL 10min），重定向到 TT 授权页时带上
- /oauth/tts/callback 调 consume_state → **原子** GETDEL，不存在或过期就拒绝

为什么重要：
- 防 CSRF：攻击者构造 /oauth/tts/callback?code=xxx 链接诱骗已登录的管理员点击，如果没有 state 就会被直接授权
- 防重放：GETDEL 确保一个 state 只能用一次

存储：**用 state 本身做 Redis key**，值填个占位；未来要关联发起人信息就把 value 换成 JSON。
"""

from __future__ import annotations

import logging
import secrets

from redis.asyncio import Redis


logger = logging.getLogger(__name__)


STATE_KEY_TMPL = "oauth:tts:state:{state}"


async def issue_state(redis: Redis, *, ttl_seconds: int = 600) -> str:
    state = secrets.token_urlsafe(32)
    key = STATE_KEY_TMPL.format(state=state)
    await redis.setex(key, ttl_seconds, "1")
    logger.info("oauth state issued ttl=%ds", ttl_seconds)
    return state


async def consume_state(redis: Redis, state: str) -> bool:
    """原子消费一次性 state。合法且未过期返回 True；否则 False。"""
    if not state:
        return False
    key = STATE_KEY_TMPL.format(state=state)
    # GETDEL 是 Redis 6.2+ 原子操作，拿到值的同时删除
    value = await redis.getdel(key)
    ok = value is not None
    logger.info("oauth state consumed ok=%s", ok)
    return ok
