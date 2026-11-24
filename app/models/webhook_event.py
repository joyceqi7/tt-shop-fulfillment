"""Webhook 原始事件记录（幂等键 + 审计）。

接收 TT 推送后：
1. 用 `dedup_key` 幂等入库（unique 冲突即忽略）
2. 冲突忽略的直接返回 200，不重复投递队列
3. 新 event 才 enqueue 到 arq 工作流

字段含义：
- dedup_key: 幂等键。优先用 TT 的 `tts_notification_id`；没有就 SHA1(body)
- raw_payload: TT 推送的 JSON 原文
- raw_headers: 收到时的 HTTP header（验签证据 + 排查）
- status: received → queued → processed / failed
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


WEBHOOK_STATUSES = (
    "received",    # 刚落库，等入队
    "queued",      # 已入 arq
    "retrying",    # arq 重试中（非最后一次 attempt 失败）
    "processed",   # 最终处理成功
    "failed",      # 业务错误（non-retryable）直接失败
    "dead",        # 重试耗尽，进死信（触发告警）
    "ignored",     # 无 order_id 等无效 payload
)


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    dedup_key: Mapped[str] = mapped_column(String(128), nullable=False)

    event_type: Mapped[str | None] = mapped_column(String(64), index=True)
    tts_notification_id: Mapped[str | None] = mapped_column(String(128))
    shop_id: Mapped[str | None] = mapped_column(String(64), index=True)
    order_id: Mapped[str | None] = mapped_column(String(64), index=True)
    timestamp_tt: Mapped[int | None] = mapped_column(BigInteger)

    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    raw_headers: Mapped[dict | None] = mapped_column(JSONB)
    signature: Mapped[str | None] = mapped_column(String(256))

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="received")

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = ({},)

    def __repr__(self) -> str:
        return (
            f"<WebhookEvent id={self.id} type={self.event_type} "
            f"order={self.order_id} status={self.status}>"
        )
