"""订单与行订单表。

TT 推送的 ORDER_STATUS_CHANGE webhook 会带 order_id，webhook handler
会去 Get Order Detail 拿到完整订单对象（含 order_line_items），
upsert 到这两张表。

raw_payload 存一份 TT 返回的原始 JSON，出 bug 时可以回溯。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


ORDER_STATUSES = (
    "AWAITING_SHIPMENT",
    "PARTIALLY_SHIPPING",
    "DELIVERED",
    "COMPLETED",
    "CANCELLED",
    "RETURN",
    "UNKNOWN",
)

ORDER_LINE_STATUSES = ("pending", "shipped", "failed", "cancelled", "refunded")


class TTSOrder(Base, TimestampMixin):
    __tablename__ = "tts_orders"

    order_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    shop_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tts_shops.shop_id"), nullable=False, index=True
    )

    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    buyer_email_encrypted: Mapped[bytes | None] = mapped_column(
        comment="PII，Fernet 加密后落库"
    )

    order_line_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_amount: Mapped[int | None] = mapped_column(
        BigInteger, comment="金额最小单位（美分）"
    )
    currency: Mapped[str | None] = mapped_column(String(8))

    create_time_tt: Mapped[int | None] = mapped_column(
        BigInteger, comment="TT 返回的订单创建 Unix 时间"
    )
    update_time_tt: Mapped[int | None] = mapped_column(
        BigInteger, index=True, comment="TT 返回的订单更新 Unix 时间"
    )
    sla_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        comment="履约截止（例如 AWAITING_SHIPMENT → DELIVERED 的平台 SLA）",
    )

    raw_payload: Mapped[dict | None] = mapped_column(JSONB)

    lines: Mapped[list["TTSOrderLine"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<TTSOrder id={self.order_id} status={self.status} shop={self.shop_id}>"


class TTSOrderLine(Base, TimestampMixin):
    __tablename__ = "tts_order_lines"

    order_line_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tts_orders.order_id"), nullable=False, index=True
    )

    seller_sku: Mapped[str | None] = mapped_column(String(128), index=True)
    tts_product_id: Mapped[str | None] = mapped_column(String(64))
    tts_sku_id: Mapped[str | None] = mapped_column(String(64))

    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unit_price: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str | None] = mapped_column(String(8))

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")

    redeemed_code_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("codes.id"), index=True
    )
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_reason: Mapped[str | None] = mapped_column(Text)

    order: Mapped[TTSOrder] = relationship(back_populates="lines")

    def __repr__(self) -> str:
        return (
            f"<TTSOrderLine id={self.order_line_id} order={self.order_id} "
            f"status={self.status} sku={self.seller_sku!r}>"
        )
