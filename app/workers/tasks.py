"""arq 任务定义。

`fulfill_order(webhook_event_id)` —— webhook 触发的履约：
1. 从 webhook_events 拿 order_id / shop_id
2. 定位 TTSShop（需要 shop_cipher）
3. 拿订单级 Redis 锁
4. 调 `fulfillment.fulfill_order_once(...)`
5. 标 webhook_events 为 processed / failed，commit

`reconciliation_task()` —— cron 触发的兜底对账（每 10 分钟）：
1. 拿 Redis 全局单实例锁（拿不到就跳过）
2. 遍历所有 shop，从每店 last_ts 起分页拉 get_order_list
3. 每个订单走 order_lock + fulfill_order_once（和 webhook worker 互斥、幂等）
4. 推进 last_ts（仅当本轮完整跑完）

错误策略：
- 业务异常（shop 找不到、payload 解析错）→ 标 failed，不抛（避免 arq 无限重试）
- 网络/TT API 异常 → 抛出，arq 按 WorkerSettings.max_tries 指数退避重试
- 锁超时 → 抛出，arq 重试（下一次可能已经被别的 worker 做完了）
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from redis.asyncio import Redis
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.token_manager import TokenManager
from app.config import get_settings
from app.db.session import session_factory
from app.models.shop import TTSShop
from app.models.webhook_event import WebhookEvent
from app.observability import capture_exception, init_sentry
from app.services.alerts import alert_dead_letter, send_feishu_text
from app.services.fulfillment import fulfill_order_once, reset_failed_lines_for_reship
from app.services.order_lock import order_lock
from app.services.metrics import collect_snapshot, evaluate_alerts
from app.services.stale_shipment import run_stale_shipment_alerts
from app.services.reconciliation import (
    ReconciliationAlreadyRunning,
    acquire_run_lock,
    run_reconciliation,
)
from app.services.tts_client import TTSClient, TTSApiError


logger = logging.getLogger(__name__)


async def _mark_event(
    session: AsyncSession,
    event_id: int,
    status: str,
    *,
    error: str | None = None,
) -> None:
    await session.execute(
        update(WebhookEvent)
        .where(WebhookEvent.id == event_id)
        .values(
            status=status,
            processed_at=datetime.now(tz=timezone.utc),
            error=error,
        )
    )


async def _resolve_shop(session: AsyncSession, event: WebhookEvent) -> TTSShop | None:
    """按 event.shop_id 查；找不到（或 event 没 shop_id）且系统只有 1 个店，则用它。"""
    if event.shop_id:
        shop = await session.get(TTSShop, event.shop_id)
        if shop is not None:
            return shop

    from sqlalchemy import select

    shops = (await session.execute(select(TTSShop))).scalars().all()
    if len(shops) == 1:
        logger.info(
            "event %s shop_id=%r not found, defaulting to sole shop %s",
            event.id,
            event.shop_id,
            shops[0].shop_id,
        )
        return shops[0]
    return None


def _is_non_retryable(exc: BaseException) -> bool:
    """判断异常是否"无需重试"。

    规则：
    - TTSApiError.is_retryable == False（TT 业务错误：订单不存在、参数错等）
    - ValueError / TypeError：通常是 payload 解析错 / 代码 bug，再多重试也不变
    其他一律视为 retryable（网络、超时、DB 瞬断、锁超时、未知）。
    """
    if isinstance(exc, TTSApiError):
        return not exc.is_retryable
    if isinstance(exc, (ValueError, TypeError)):
        return True
    return False


async def fulfill_order(ctx: dict, webhook_event_id: int) -> dict:
    settings = get_settings()
    sf = session_factory()
    redis = Redis.from_url(settings.redis_url)

    job_try = int(ctx.get("job_try", 1) or 1)
    max_tries = int(ctx.get("max_tries", 5) or 5)
    is_last_attempt = job_try >= max_tries

    try:
        # Phase 1: 读 event + 定位 shop（短事务）
        async with sf() as session:
            event = await session.get(WebhookEvent, webhook_event_id)
            if event is None:
                logger.warning("fulfill_order: event %s not found", webhook_event_id)
                return {"status": "missing", "event_id": webhook_event_id}

            if not event.order_id:
                await _mark_event(
                    session,
                    webhook_event_id,
                    "ignored",
                    error="no order_id in payload",
                )
                await session.commit()
                return {"status": "no_order_id", "event_id": webhook_event_id}

            shop = await _resolve_shop(session, event)
            if shop is None:
                await _mark_event(
                    session,
                    webhook_event_id,
                    "failed",
                    error=f"shop not found for event.shop_id={event.shop_id!r}",
                )
                await session.commit()
                return {"status": "no_shop", "event_id": webhook_event_id}

            order_id = event.order_id
            shop_id = shop.shop_id
            event_shop_id = event.shop_id
            event_type = event.event_type

        # Phase 2: 加订单锁，调发货编排（service 自管事务）
        async with httpx.AsyncClient(
            proxy=settings.tts_http_proxy or None, trust_env=False, timeout=30
        ) as http:
            token_manager = TokenManager(settings, sf, redis, http_client=http)
            client = TTSClient(settings, token_manager, http_client=http)

            async with order_lock(redis, order_id):
                async with sf() as s:
                    shop = await s.get(TTSShop, shop_id)
                if shop is None:
                    raise RuntimeError(f"shop {shop_id} disappeared between phases")

                try:
                    if event_type == "ADMIN_RESHIP":
                        reset_lines = await reset_failed_lines_for_reship(
                            sf, order_id=order_id
                        )
                        if reset_lines:
                            logger.info(
                                "admin reship reset %d failed lines order=%s lines=%s",
                                len(reset_lines),
                                order_id,
                                reset_lines,
                            )
                    settings = get_settings()
                    result = await fulfill_order_once(
                        session_factory=sf,
                        client=client,
                        shop=shop,
                        order_id=order_id,
                        supplier_delivery_timeout_seconds=settings.supplier_delivery_timeout_seconds,
                    )
                except Exception as e:
                    err_str = f"attempt {job_try}/{max_tries} {type(e).__name__}: {e}"
                    logger.exception(
                        "fulfill_order raised for event %s order %s (%s)",
                        webhook_event_id,
                        order_id,
                        err_str,
                    )

                    non_retryable = _is_non_retryable(e)
                    # 三种终态：non_retryable → failed；最后一次 → dead；否则 retrying
                    if non_retryable:
                        final_status = "failed"
                    elif is_last_attempt:
                        final_status = "dead"
                    else:
                        final_status = "retrying"

                    async with sf() as s_err:
                        await _mark_event(s_err, webhook_event_id, final_status, error=err_str)
                        await s_err.commit()

                    # 告警：non_retryable 立刻报；retryable 只在真正进 dead 时报（避免骚扰）
                    if final_status in ("failed", "dead"):
                        capture_exception(
                            e,
                            webhook_event_id=str(webhook_event_id),
                            order_id=order_id,
                            shop_id=shop_id,
                            final_status=final_status,
                        )
                        await alert_dead_letter(
                            event_id=webhook_event_id,
                            order_id=order_id,
                            shop_id=event_shop_id or shop_id,
                            attempt=job_try,
                            max_tries=max_tries,
                            error=err_str,
                        )

                    if non_retryable:
                        # 不抛：arq 认为任务已完成（避免无意义重试）
                        return {
                            "status": "failed",
                            "event_id": webhook_event_id,
                            "error": err_str,
                        }
                    raise  # retryable：抛给 arq 按退避重试

                async with sf() as s:
                    await _mark_event(s, webhook_event_id, "processed")
                    await s.commit()

        logger.info(
            "fulfill_order done event_id=%s order=%s result=%s",
            webhook_event_id,
            order_id,
            result,
        )
        return result
    finally:
        await redis.aclose()


async def watchdog_task(ctx: dict) -> dict:
    """健康巡检（cron 每 5 分钟）：查 metrics snapshot，把超阈值的指标发飞书告警。

    和 `/metrics` endpoint 共用同一份 `collect_snapshot`；不做任何数据订正
    （僵尸锁仅告警不自动 release —— MVP 保守策略，避免和还在运行的 worker 冲突）。
    """
    settings = get_settings()
    sf = session_factory()
    redis = Redis.from_url(settings.redis_url)

    try:
        snap = await collect_snapshot(session_factory=sf, redis=redis)
        alerts = evaluate_alerts(snap)
        stale_stats = await run_stale_shipment_alerts(
            session_factory=sf,
            redis=redis,
            settings=settings,
        )

        if not alerts and not stale_stats.get("sent"):
            logger.info(
                "watchdog OK: webhook=%s free_skus=%d stuck=%d lag=%s stale_checked=%s",
                snap.webhook_events_by_status,
                len(snap.codes_free_by_sku),
                snap.codes_stuck_locked,
                snap.reconciliation_lag_seconds,
                stale_stats.get("checked", 0),
            )
            return {
                "status": "ok",
                "alerts": 0,
                "stale_shipment": stale_stats,
            }

        if alerts:
            text = "[TT-Shop 健康巡检告警]\n" + "\n".join(f"- {a}" for a in alerts)
            logger.warning("watchdog raising %d alerts", len(alerts))
            await send_feishu_text(text)

        return {
            "status": "alerted" if alerts or stale_stats.get("sent") else "ok",
            "alerts": len(alerts),
            "stale_shipment": stale_stats,
        }
    finally:
        await redis.aclose()


async def reconciliation_task(ctx: dict) -> dict:
    """cron 触发的兜底对账。每 10 分钟跑一次（见 WorkerSettings.cron_jobs）。"""
    return await _run_reconciliation(buffer_seconds=None)


async def manual_reconciliation_task(ctx: dict) -> dict:
    """后台按钮触发的手动订单同步。扫到当前时间，避免运营等待默认缓冲。"""
    return await _run_reconciliation(buffer_seconds=0)


async def _run_reconciliation(*, buffer_seconds: int | None) -> dict:
    settings = get_settings()
    sf = session_factory()
    redis = Redis.from_url(settings.redis_url)

    try:
        try:
            async with acquire_run_lock(redis):
                async with httpx.AsyncClient(
                    proxy=settings.tts_http_proxy or None, trust_env=False, timeout=30
                ) as http:
                    token_manager = TokenManager(settings, sf, redis, http_client=http)
                    client = TTSClient(settings, token_manager, http_client=http)
                    kwargs = (
                        {"buffer_seconds": buffer_seconds}
                        if buffer_seconds is not None
                        else {}
                    )
                    stats = await run_reconciliation(
                        session_factory=sf,
                        redis=redis,
                        client=client,
                        **kwargs,
                    )
                logger.info("reconciliation done: %s", stats)
                return stats
        except ReconciliationAlreadyRunning:
            logger.info("reconciliation already running in another instance, skip")
            return {"status": "skipped", "reason": "another_instance_running"}
    finally:
        await redis.aclose()


async def price_guard_task(ctx: dict) -> dict:
    """cron 调价倒挂预警：比对 TT 售价与 WGCards 实时采购价。"""
    settings = get_settings()
    sf = session_factory()
    redis = Redis.from_url(settings.redis_url)

    try:
        from app.services.price_guard import run_price_guard

        stats = await run_price_guard(
            session_factory=sf,
            redis=redis,
            settings=settings,
        )
        return stats
    finally:
        await redis.aclose()
