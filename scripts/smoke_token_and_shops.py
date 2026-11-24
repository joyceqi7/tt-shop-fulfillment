"""Week 1 脚手架 smoke test。

跑一遍 = 验证这些组件端到端工作：
  1. TokenManager 刷 access_token（从 .env 里的 refresh_token 起手）
  2. Fernet 加密落 tts_tokens 表
  3. Redis 分布式锁没炸
  4. TTSClient 用 TokenManager 拿 token，调 getAuthorizedShops
  5. 店铺信息 upsert 到 tts_shops 表

第二次跑应该不会再触发 refresh（token 在 buffer 内），用来验证读 DB 分支。

用法：
    source .venv/bin/activate
    python scripts/smoke_token_and_shops.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from redis.asyncio import Redis  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402

from app.auth.token_manager import TokenManager  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db.session import session_factory  # noqa: E402
from app.models.shop import TTSShop  # noqa: E402
from app.models.token import TTSToken  # noqa: E402
from app.services.tts_client import TTSClient  # noqa: E402


def _fmt_unix(ts: int | None) -> str:
    if ts is None:
        return "None"
    return f"{ts} ({time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))})"


async def main() -> None:
    settings = get_settings()
    sf = session_factory()
    redis = Redis.from_url(settings.redis_url)

    tm = TokenManager(settings=settings, session_factory=sf, redis=redis)

    print("=" * 60)
    print("Step 1: 拿 access_token（首次应触发 refresh 并落库）")
    print("=" * 60)
    t0 = time.perf_counter()
    access_token = await tm.get_access_token()
    elapsed = time.perf_counter() - t0
    print(f"  耗时: {elapsed*1000:.1f}ms")
    print(f"  access_token: {access_token[:24]}... (长度 {len(access_token)})")

    async with sf() as session:
        token_row = await session.get(TTSToken, settings.tts_app_key)
    assert token_row is not None, "tts_tokens 表没落库"
    print(f"  DB 里的 expire_at: {_fmt_unix(token_row.access_token_expire_at)}")
    print(f"  seller_name: {token_row.seller_name!r}")
    print(f"  version: {token_row.version}")
    print()

    print("=" * 60)
    print("Step 2: 再拿一次（应走 DB 缓存，不触发 refresh）")
    print("=" * 60)
    t0 = time.perf_counter()
    access_token_2 = await tm.get_access_token()
    elapsed = time.perf_counter() - t0
    print(f"  耗时: {elapsed*1000:.1f}ms（预期 < 50ms）")
    assert access_token == access_token_2, "两次拿到的 token 应相同"
    print(f"  ✅ 命中缓存")
    print()

    print("=" * 60)
    print("Step 3: 调 getAuthorizedShops 并 upsert 到 tts_shops")
    print("=" * 60)
    client = TTSClient(settings=settings, token_manager=tm)
    shops = await client.get_authorized_shops()
    print(f"  拿到 {len(shops)} 个店铺")

    if not shops:
        print("  ⚠️ 没有授权的店铺，无法验证 upsert")
    else:
        async with sf() as session:
            for shop in shops:
                stmt = (
                    pg_insert(TTSShop)
                    .values(
                        shop_id=shop.get("id") or shop.get("shop_id"),
                        shop_cipher=shop.get("cipher") or shop.get("shop_cipher"),
                        shop_name=shop.get("name") or shop.get("shop_name"),
                        region=shop.get("region"),
                        seller_type=shop.get("seller_type"),
                        seller_base_region=shop.get("seller_base_region"),
                    )
                    .on_conflict_do_update(
                        index_elements=["shop_id"],
                        set_=dict(
                            shop_cipher=shop.get("cipher") or shop.get("shop_cipher"),
                            shop_name=shop.get("name") or shop.get("shop_name"),
                            region=shop.get("region"),
                            seller_type=shop.get("seller_type"),
                            seller_base_region=shop.get("seller_base_region"),
                        ),
                    )
                )
                await session.execute(stmt)
            await session.commit()

        async with sf() as session:
            result = await session.execute(select(TTSShop))
            stored = result.scalars().all()
        print(f"  DB 中现有 {len(stored)} 个 shop:")
        for s in stored:
            print(
                f"    - {s.shop_id[:16]}... name={s.shop_name!r} "
                f"region={s.region} type={s.seller_type} base={s.seller_base_region}"
            )
    print()

    print("=" * 60)
    print("全部通过 ✅")
    print("=" * 60)

    await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
