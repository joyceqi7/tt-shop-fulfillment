"""对 TT 发起的每一次 API 调用的审计日志。

主要用途：
- Redeem 回传失败排查（看 request/response）
- 合规：用户投诉"没收到码"时的证据链
- 告警：某段时间 error_code 聚集 → 触发飞书告警
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FulfillmentLog(Base):
    __tablename__ = "fulfillment_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    order_id: Mapped[str | None] = mapped_column(String(64), index=True)
    order_line_id: Mapped[str | None] = mapped_column(String(64), index=True)
    shop_id: Mapped[str | None] = mapped_column(String(64), index=True)

    api_endpoint: Mapped[str] = mapped_column(String(256), nullable=False)
    api_method: Mapped[str] = mapped_column(String(8), nullable=False, default="POST")

    request_params: Mapped[dict | None] = mapped_column(JSONB)
    request_body: Mapped[dict | None] = mapped_column(JSONB)
    response_body: Mapped[dict | None] = mapped_column(JSONB)

    http_status: Mapped[int | None] = mapped_column(Integer, index=True)
    api_code: Mapped[int | None] = mapped_column(Integer, index=True)
    api_message: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(
        String(128), index=True, comment="TT 返回的 request_id，便于找客服"
    )

    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(
        Text, comment="客户端异常信息（网络错等本地错误）"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<FulfillmentLog id={self.id} order={self.order_id} "
            f"endpoint={self.api_endpoint} code={self.api_code}>"
        )
