"""供应商采购状态表。

记录实时向供应商（如 WGCards）采购的状态，用于追踪采购进度和卡密获取。
每条记录对应一个 TT 订单行（order_line_id）的供应商采购流程。

状态机：
    pending ──下单成功──> placed
    placed ──开始处理──> processing
    processing ──卡密就绪──> card_ready
    card_ready ──发货成功──> shipped
    placed/processing ──超时──> timeout
    placed/processing/card_ready ──失败──> failed
    任意 ──管理员取消──> cancelled
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


SUPPLIER_ORDER_STATUSES = (
    "pending",
    "placed",
    "processing",
    "card_ready",
    "imported",
    "shipped",
    "timeout",
    "failed",
    "cancelled",
)

PROCUREMENT_MODES = ("realtime", "bulk")


class SupplierOrder(Base, TimestampMixin):
    """供应商采购订单表。"""

    __tablename__ = "supplier_orders"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    supplier_id: Mapped[int] = mapped_column(
        ForeignKey("suppliers.id"), nullable=False, index=True
    )

    tt_order_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
        comment="TT 订单 ID（实时采购）",
    )
    tt_order_line_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
        comment="TT 订单行 ID（实时采购）",
    )

    procurement_mode: Mapped[str] = mapped_column(
        String(16),
        default="realtime",
        nullable=False,
        index=True,
        comment="realtime | bulk",
    )
    purchase_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("purchase_orders.id"),
        nullable=True,
        index=True,
        comment="批量采购关联 PO",
    )

    supplier_order_id: Mapped[str | None] = mapped_column(
        String(64),
        comment="供应商订单号（WGCards orderId）",
    )
    service_order: Mapped[str | None] = mapped_column(
        String(128),
        index=True,
        comment="外部服务订单号（用于防重）",
    )

    status: Mapped[str] = mapped_column(
        String(16),
        default="pending",
        nullable=False,
        index=True,
    )

    sku_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="供应商 SKU ID",
    )
    sku_name: Mapped[str | None] = mapped_column(
        String(256),
        comment="SKU 名称",
    )
    face_value: Mapped[float | None] = mapped_column(
        Float,
        comment="购买面值",
    )
    buy_num: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )

    currency: Mapped[str] = mapped_column(
        String(8),
        default="USD",
        nullable=False,
    )
    total_amount: Mapped[float | None] = mapped_column(
        Float,
        comment="实际支付金额",
    )

    card_codes: Mapped[list[str] | None] = mapped_column(
        ARRAY(String(256)),
        comment="获取到的卡密列表",
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        comment="失败原因",
    )
    raw_response: Mapped[str | None] = mapped_column(
        Text,
        comment="原始响应 JSON（用于排查）",
    )

    placed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        comment="下单时间",
    )
    card_ready_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        comment="卡密就绪时间",
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        comment="完成时间",
    )
    elapsed_seconds: Mapped[float | None] = mapped_column(
        Float,
        comment="从下单到完成的耗时（秒）",
    )

    supplier: Mapped["Supplier"] = relationship(  # noqa: F821
        back_populates="supplier_orders",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ("
            + ", ".join(f"'{s}'" for s in SUPPLIER_ORDER_STATUSES)
            + ")",
            name="supplier_order_status",
        ),
        CheckConstraint(
            "procurement_mode IN ('realtime', 'bulk')",
            name="supplier_order_procurement_mode",
        ),
        Index("ix_supplier_orders_tt_line", "tt_order_id", "tt_order_line_id"),
        Index(
            "ix_supplier_orders_pending",
            "status",
            postgresql_where="status IN ('pending', 'placed', 'processing')",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<SupplierOrder id={self.id} status={self.status} "
            f"tt_order={self.tt_order_id} supplier_order={self.supplier_order_id}>"
        )
