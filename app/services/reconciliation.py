"""兜底对账 Job。

**目的**：webhook 并非 100% 可靠——TT 推送可能丢、我们偶尔 504、新店刚授权
时还没配 webhook。对账 Job 每 10 分钟定时拉 `get_order_list`，扫出所有
"这段时间里 update_time 变过的订单"，对每个订单都触发一次 `fulfill_order_once`。

幂等的承诺由 `fulfill_order_once` 自己保证：
- `_ship` 只处理 status=pending 的 line（已 shipped 会被过滤）
- CANCEL / RETURN 看码当前 status 决定 release / void

对账 Job 本身不重新实现发货逻辑，只负责"发现订单 + 保证并发安全"。

**并发防护**：
- 全局单实例锁（Redis, NX + EX），多 replica 部署只有一个实例真跑
- 每订单进 `order_lock`，和 webhook worker 互斥
- 分页之间 `asyncio.sleep(0.5s)`，初步限流；后续上令牌桶再改

**失败策略**：
- 单订单失败：记 stats["errors"]，继续下一单
- 单店累计 5 个订单失败：放弃这个店本次对账（可能 TT 在抖动），不推进 last_ts
- `get_order_list` 自身失败：不推进 last_ts，下次重试整段时间窗
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import LockError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.shop import TTSShop
from app.services.fulfillment import TTSClientLike, fulfill_order_once
from app.services.order_lock import order_lock


logger = logging.getLogger(__name__)


RECONCILIATION_LOCK_KEY = "reconciliation:run"
RECONCILIATION_LOCK_TTL = 600  # 10 min，够对账跑完
LAST_TS_KEY_TPL = "reconciliation:last_ts:{shop_id}"
DEFAULT_LOOKBACK_SECONDS = 3 * 24 * 3600  # 初次运行往前拉 3 天
BUFFER_SECONDS = 300  # update_time_lt = now - 5 分钟，避开 TT 刚写入时的读后不一致
OVERLAP_SECONDS = 3600  # 每轮重扫最近 1 小时，兜住 TT Open API 延迟可见/晚更新
PAGE_SIZE = 50
PAGE_SLEEP_SECONDS = 0.5
MAX_ERRORS_PER_SHOP = 5


class ReconciliationAlreadyRunning(RuntimeError):
    """另一个实例/线程正在跑对账，本次跳过。"""


@asynccontextmanager
async def acquire_run_lock(redis: Redis):
    """全局对账 Job 单实例锁。拿不到即跳过（不排队）。"""
    lock = redis.lock(
        RECONCILIATION_LOCK_KEY,
        timeout=RECONCILIATION_LOCK_TTL,
        blocking_timeout=0,
    )
    acquired = await lock.acquire(blocking=False)
    if not acquired:
        raise ReconciliationAlreadyRunning()

    logger.info("reconciliation run lock acquired")
    try:
        yield
    finally:
        try:
            await lock.release()
        except LockError:
            logger.warning("reconciliation run lock already expired before release")


async def _load_last_ts(redis: Redis, shop_id: str) -> int:
    raw = await redis.get(LAST_TS_KEY_TPL.format(shop_id=shop_id))
    if raw is None:
        return int(time.time()) - DEFAULT_LOOKBACK_SECONDS
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(time.time()) - DEFAULT_LOOKBACK_SECONDS


async def _save_last_ts(redis: Redis, shop_id: str, ts: int) -> None:
    await redis.set(LAST_TS_KEY_TPL.format(shop_id=shop_id), str(ts))


async def run_reconciliation(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    redis: Redis,
    client: TTSClientLike,
    now: int | None = None,
    buffer_seconds: int = BUFFER_SECONDS,
) -> dict[str, Any]:
    """跑一次对账。外层保证单实例锁。"""
    now_ts = now or int(time.time())
    till = now_ts - max(0, buffer_seconds)

    async with session_factory() as s:
        shops = list((await s.execute(select(TTSShop))).scalars())

    stats: dict[str, Any] = {
        "now_ts": now_ts,
        "till": till,
        "buffer_seconds": buffer_seconds,
        "shops_total": len(shops),
        "shops_processed": 0,
        "shops_skipped": 0,
        "orders_scanned": 0,
        "orders_fulfilled": 0,
        "orders_failed": 0,
        "errors": [],
    }

    for shop in shops:
        saved_since = await _load_last_ts(redis, shop.shop_id)
        since = max(0, saved_since - OVERLAP_SECONDS)
        if since >= till:
            logger.info(
                "shop %s: since(%d) >= till(%d), skip", shop.shop_id, since, till
            )
            stats["shops_skipped"] += 1
            continue

        logger.info(
            "shop %s: reconciling window [%d, %d), saved_since=%d overlap=%ds",
            shop.shop_id,
            since,
            till,
            saved_since,
            OVERLAP_SECONDS,
        )
        completed = await _reconcile_shop(
            session_factory=session_factory,
            redis=redis,
            client=client,
            shop=shop,
            since=since,
            till=till,
            stats=stats,
        )
        stats["shops_processed"] += 1

        if completed:
            await _save_last_ts(redis, shop.shop_id, till)
            logger.info("shop %s: last_ts advanced to %d", shop.shop_id, till)
        else:
            logger.warning(
                "shop %s: reconcile incomplete, last_ts NOT advanced", shop.shop_id
            )

    return stats


async def _reconcile_shop(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    redis: Redis,
    client: TTSClientLike,
    shop: TTSShop,
    since: int,
    till: int,
    stats: dict[str, Any],
) -> bool:
    """扫一个店的订单。全部成功返回 True；中途放弃返回 False。"""
    page_token: str | None = None
    errors_in_shop = 0
    page_index = 0

    while True:
        page_index += 1
        try:
            resp = await client.get_order_list(
                shop.shop_cipher,
                page_size=PAGE_SIZE,
                page_token=page_token,
                update_time_ge=since,
                update_time_lt=till,
            )
        except Exception as e:
            logger.exception("get_order_list failed shop=%s page=%d", shop.shop_id, page_index)
            stats["errors"].append(
                {"shop_id": shop.shop_id, "stage": "get_order_list", "error": str(e)}
            )
            return False

        data = resp.get("data") or {}
        orders = data.get("orders") or []
        next_page_token = data.get("next_page_token")

        logger.info(
            "shop %s page %d: got %d orders", shop.shop_id, page_index, len(orders)
        )

        for order in orders:
            order_id = str(order.get("id") or order.get("order_id") or "")
            if not order_id:
                continue
            stats["orders_scanned"] += 1

            try:
                async with order_lock(redis, order_id):
                    result = await fulfill_order_once(
                        session_factory=session_factory,
                        client=client,
                        shop=shop,
                        order_id=order_id,
                    )
            except Exception as e:
                logger.exception("reconcile order %s failed", order_id)
                stats["errors"].append(
                    {"shop_id": shop.shop_id, "order_id": order_id, "error": str(e)}
                )
                stats["orders_failed"] += 1
                errors_in_shop += 1
                if errors_in_shop >= MAX_ERRORS_PER_SHOP:
                    logger.error(
                        "shop %s: too many per-order errors (%d), abort this run",
                        shop.shop_id,
                        errors_in_shop,
                    )
                    return False
                continue

            if result.get("status") in ("shipped", "cancelled", "return"):
                stats["orders_fulfilled"] += 1

        if not next_page_token:
            break
        page_token = next_page_token
        await asyncio.sleep(PAGE_SLEEP_SECONDS)

    return True
