"""未发货超时告警。

订单状态仍为 AWAITING_SHIPMENT / PARTIALLY_SHIPPING，且存在 pending 行订单，
自 TT 下单时间（或入库 created_at）起超过阈值 → 飞书告警（每单 24h 内不重复）。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from redis.asyncio import Redis
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.models.order import TTSOrder, TTSOrderLine
from app.services.alerts import send_feishu_text

logger = logging.getLogger(__name__)

_ALERT_TTL_SECONDS = 86400  # 24h 内同订单不重复告警
_REDIS_KEY_TPL = "stale_shipment:alerted:{order_id}"


@dataclass(slots=True)
class StaleOrderRow:
    order_id: str
    status: str
    pending_lines: int
    age_minutes: int


def _order_age_seconds(
    *,
    create_time_tt: int | None,
    created_at: datetime,
    now_ts: int,
) -> int:
    if create_time_tt is not None and create_time_tt > 0:
        return max(0, now_ts - int(create_time_tt))
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return max(0, now_ts - int(created_at.timestamp()))


async def find_stale_unshipped_orders(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    settings: Settings | None = None,
) -> list[StaleOrderRow]:
    """查询超过阈值仍未发货的订单。"""
    settings = settings or get_settings()
    now_ts = int(time.time())
    threshold = settings.stale_shipment_alert_seconds
    cutoff_ts = now_ts - threshold
    cutoff_dt = datetime.now(timezone.utc) - timedelta(seconds=threshold)

    async with session_factory() as db:
        rows = (
            await db.execute(
                select(
                    TTSOrder.order_id,
                    TTSOrder.status,
                    TTSOrder.create_time_tt,
                    TTSOrder.created_at,
                    func.count(TTSOrderLine.order_line_id).label("pending_lines"),
                )
                .join(
                    TTSOrderLine,
                    and_(
                        TTSOrderLine.order_id == TTSOrder.order_id,
                        TTSOrderLine.status == "pending",
                    ),
                )
                .where(
                    TTSOrder.status.in_(("AWAITING_SHIPMENT", "PARTIALLY_SHIPPING")),
                    or_(
                        and_(
                            TTSOrder.create_time_tt.isnot(None),
                            TTSOrder.create_time_tt < cutoff_ts,
                        ),
                        and_(
                            TTSOrder.create_time_tt.is_(None),
                            TTSOrder.created_at < cutoff_dt,
                        ),
                    ),
                )
                .group_by(
                    TTSOrder.order_id,
                    TTSOrder.status,
                    TTSOrder.create_time_tt,
                    TTSOrder.created_at,
                )
                .order_by(TTSOrder.create_time_tt.asc().nullsfirst())
            )
        ).all()

    result: list[StaleOrderRow] = []
    for row in rows:
        age_sec = _order_age_seconds(
            create_time_tt=row.create_time_tt,
            created_at=row.created_at,
            now_ts=now_ts,
        )
        if age_sec < threshold:
            continue
        result.append(
            StaleOrderRow(
                order_id=row.order_id,
                status=row.status,
                pending_lines=int(row.pending_lines),
                age_minutes=age_sec // 60,
            )
        )
    return result


async def run_stale_shipment_alerts(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    redis: Redis,
    settings: Settings | None = None,
) -> dict[str, int | bool]:
    """扫描未发货超时订单并发送飞书（Redis 去重）。"""
    settings = settings or get_settings()
    stale = await find_stale_unshipped_orders(session_factory, settings=settings)
    if not stale:
        return {"checked": 0, "alerted": 0, "sent": False}

    hours = settings.stale_shipment_alert_seconds // 3600
    new_rows: list[StaleOrderRow] = []
    for row in stale:
        key = _REDIS_KEY_TPL.format(order_id=row.order_id)
        if await redis.set(key, "1", nx=True, ex=_ALERT_TTL_SECONDS):
            new_rows.append(row)

    if not new_rows:
        logger.info(
            "stale_shipment: %d stale orders, all already alerted within 24h",
            len(stale),
        )
        return {"checked": len(stale), "alerted": 0, "sent": False}

    lines = [
        f"- {r.order_id} · {r.status} · pending={r.pending_lines} · 已等待 {r.age_minutes} 分钟"
        for r in new_rows
    ]
    text = (
        f"【未发货超时告警】以下订单超过 {hours} 小时仍有未发货行：\n"
        + "\n".join(lines)
    )
    logger.warning("stale_shipment alerting %d orders", len(new_rows))
    sent = await send_feishu_text(text)
    return {"checked": len(stale), "alerted": len(new_rows), "sent": sent}
