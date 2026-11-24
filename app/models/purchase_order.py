"""采购单（PO）表。

记录"我们什么时候、从谁、以什么单价、采购了多少张某 SKU 的卡"，
是计算成本和应付账款的源数据。

关键字段：
- `unit_cost` / `total_cost` 都是美分整数，避免浮点精度问题
- `payment_status` 简单 4 态：unpaid → partial → paid，逾期未付转 overdue（由后台 cron / 触发器写入）
- `batch_id` 与 `codes.batch_id` 对齐：一张 PO 对应一次 CSV 导入产出的所有 codes
- 历史 codes（4-23 cutover 之前）`purchase_order_id=NULL`，财务模块以"无成本数据"处理

状态机：
    unpaid ──标记部分付款──> partial
    unpaid ──标记全额已付──> paid
    partial ──全额已付──> paid
    任意 ──> overdue：仅由 cron 在 payment_due_date < now 且 status != paid 时自动设
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


PO_PAYMENT_STATUSES = ("unpaid", "partial", "paid", "overdue")


class PurchaseOrder(Base, TimestampMixin):
    __tablename__ = "purchase_orders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    po_number: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        comment="人工编号或自动生成 PO20260427-001",
    )
    supplier_id: Mapped[int] = mapped_column(
        ForeignKey("suppliers.id"), nullable=False, index=True
    )

    sku_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="关联 sku_mapping.internal_sku_id；不加 FK 因为 sku 软删，避免锁约束",
    )
    face_value: Mapped[int] = mapped_column(Integer, nullable=False, comment="面额（美分）")

    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_cost: Mapped[int] = mapped_column(Integer, nullable=False, comment="单价（美分）")
    total_cost: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        comment="冗余：quantity × unit_cost，便于聚合查询不重算",
    )

    purchase_date: Mapped[date] = mapped_column(Date, nullable=False)
    payment_due_date: Mapped[date | None] = mapped_column(Date, index=True)
    amount_paid: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    payment_status: Mapped[str] = mapped_column(
        String(16), default="unpaid", nullable=False, index=True
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    batch_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        comment="对应 codes.batch_id（导入完成后填）；新建 PO 时为空",
    )

    note: Mapped[str | None] = mapped_column(Text)

    supplier: Mapped["Supplier"] = relationship(  # noqa: F821
        back_populates="purchase_orders"
    )

    __table_args__ = (
        CheckConstraint(
            "payment_status IN ("
            + ", ".join(f"'{s}'" for s in PO_PAYMENT_STATUSES)
            + ")",
            name="payment_status",
        ),
        Index("ix_purchase_orders_supplier_date", "supplier_id", "purchase_date"),
        Index(
            "ix_purchase_orders_unpaid",
            "payment_due_date",
            postgresql_where="payment_status != 'paid'",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<PurchaseOrder id={self.id} po={self.po_number} sku={self.sku_id} "
            f"qty={self.quantity} cost={self.total_cost} status={self.payment_status}>"
        )
