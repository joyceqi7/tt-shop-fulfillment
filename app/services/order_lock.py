"""订单级 Redis 分布式锁。

发货主流程（webhook → worker）和兜底对账 Job 必须互斥——同一个订单在
任何时刻只有一个 worker 在写 `tts_orders` / `tts_order_lines` / `codes`。
否则会出现：
- 同一张 free 码被两个流程同时锁定
- Redeem Callback 被重复调用
- order_lines.status 被覆盖成错误的值

实现选择：Redis SETNX with PX（`redis.asyncio.Redis.lock`），
key = `order_lock:{order_id}`。

- 锁 TTL：`timeout` 秒（防死锁；进程 crash 后自动过期）
- 等锁：最多 `blocking_timeout` 秒，超时抛 `OrderLockTimeout`
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from redis.asyncio import Redis
from redis.exceptions import LockError


logger = logging.getLogger(__name__)


class OrderLockTimeout(RuntimeError):
    def __init__(self, order_id: str) -> None:
        super().__init__(f"order lock timeout: {order_id}")
        self.order_id = order_id


@asynccontextmanager
async def order_lock(
    redis: Redis,
    order_id: str,
    *,
    timeout: int = 120,
    blocking_timeout: float = 10.0,
):
    key = f"order_lock:{order_id}"
    lock = redis.lock(key, timeout=timeout, blocking_timeout=blocking_timeout)
    acquired = await lock.acquire(blocking=True)
    if not acquired:
        raise OrderLockTimeout(order_id)

    logger.debug("order lock acquired key=%s", key)
    try:
        yield lock
    finally:
        try:
            await lock.release()
            logger.debug("order lock released key=%s", key)
        except LockError:
            logger.warning("order lock already released or expired key=%s", key)
