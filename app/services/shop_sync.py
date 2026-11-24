"""Shop 同步服务：拉 TT 授权店铺列表 + upsert 到 `tts_shops`。

从 `scripts/smoke_token_and_shops.py` 抽出来，方便：
- OAuth callback 拿到 token 后立刻 sync
- 店主重授权后手动触发 sync
- 启动 / 定时任务里健康检查 sync
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.models.shop import TTSShop
from app.services.tts_client import TTSClient


logger = logging.getLogger(__name__)


def _pick(payload: dict[str, Any], *keys: str) -> Any:
    """TT 各接口字段命名不统一（id vs shop_id、cipher vs shop_cipher），找到哪个填哪个。"""
    for k in keys:
        v = payload.get(k)
        if v is not None:
            return v
    return None


async def upsert_shops_from_tt(
    *,
    client: TTSClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> list[TTSShop]:
    """调 get_authorized_shops 后 upsert 到 tts_shops；返回最新全量 shop 列表（DB 里的）。"""
    shops = await client.get_authorized_shops()
    if not shops:
        logger.warning("TT 返回 0 个授权 shop — 检查 App 是否发布 / 店主是否授权")
        async with session_factory() as session:
            rows = (await session.execute(select(TTSShop))).scalars().all()
        return list(rows)

    async with session_factory() as session:
        for s in shops:
            shop_id = _pick(s, "id", "shop_id")
            shop_cipher = _pick(s, "cipher", "shop_cipher")
            if not shop_id or not shop_cipher:
                logger.warning("跳过残缺 shop payload: %s", s)
                continue
            fields = dict(
                shop_cipher=shop_cipher,
                shop_name=_pick(s, "name", "shop_name"),
                region=s.get("region"),
                seller_type=s.get("seller_type"),
                seller_base_region=s.get("seller_base_region"),
            )
            stmt = (
                pg_insert(TTSShop)
                .values(shop_id=shop_id, **fields)
                .on_conflict_do_update(index_elements=["shop_id"], set_=fields)
            )
            await session.execute(stmt)
        await session.commit()

    async with session_factory() as session:
        rows = (await session.execute(select(TTSShop))).scalars().all()

    logger.info("upserted shops count=%d", len(rows))
    return list(rows)
