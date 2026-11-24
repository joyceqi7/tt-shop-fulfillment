"""把 .env 里的 REDIS_URL 翻译成 arq 需要的 RedisSettings。"""

from __future__ import annotations

from urllib.parse import urlparse

from arq.connections import RedisSettings

from app.config import get_settings


def build_redis_settings() -> RedisSettings:
    url = urlparse(get_settings().redis_url)
    db_path = (url.path or "/0").lstrip("/") or "0"
    return RedisSettings(
        host=url.hostname or "localhost",
        port=url.port or 6379,
        database=int(db_path),
        password=url.password,
    )
