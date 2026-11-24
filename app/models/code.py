"""卡密库表。

几个关键设计：
- `code_encrypted` 用 Fernet 存，DB 泄了也拿不到明文
- `code_hash` 存 SHA-256，用于去重（导入时防止同一张卡重复入库）+ 必要时定位
- `status` 是字符串枚举，不用 Postgres ENUM type（alter 起来痛），而用 CHECK 约束
- 取码用 `SELECT ... FOR UPDATE SKIP LOCKED`（plan §十 并发防线）
- `locked_at` 有值但超过 N 分钟未变 used → 僵尸锁，定时任务自动解锁
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


CODE_STATUSES = ("free", "locked", "used", "void", "expired", "recycled")


class Code(Base, TimestampMixin):
    __tablename__ = "codes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    batch_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False, comment="导入批次，便于召回/审计"
    )
    sku_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="内部 SKU ID，关联 sku_mapping.internal_sku_id",
    )
    face_value: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="面额（美分）"
    )

    code_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    code_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="SHA-256 of plaintext, for dedup and lookup",
    )

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="free", index=True
    )

    order_line_id: Mapped[str | None] = mapped_column(
        String(64), index=True, comment="被哪个子单占用"
    )

    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="卡密自身有效期，与订单无关"
    )

    operator: Mapped[str | None] = mapped_column(
        String(64), comment="导入/操作人，审计"
    )
    note: Mapped[str | None] = mapped_column(Text)

    purchase_order_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("purchase_orders.id"),
        index=True,
        comment="关联采购单（成本来源）；4-23 cutover 之前的历史码为空",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ("
            + ", ".join(f"'{s}'" for s in CODE_STATUSES)
            + ")",
            name="code_status",
        ),
        Index("ix_codes_hash", "code_hash", unique=True),
    )

    def __repr__(self) -> str:
        return (
            f"<Code id={self.id} sku={self.sku_id} face={self.face_value} "
            f"status={self.status} line={self.order_line_id}>"
        )
