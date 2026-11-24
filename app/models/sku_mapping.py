"""内部 SKU ↔ TT 商品 SKU 的映射。

TT 侧一条 order_line 带 `seller_sku` / `product_id` / `sku_id`。
内部仓储按 `internal_sku_id` 组织。
发货前要先查这张表把 TT SKU 翻译成内部 SKU，再去 codes 表取码。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class SkuMapping(Base, TimestampMixin):
    __tablename__ = "sku_mapping"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    internal_sku_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    product_name: Mapped[str | None] = mapped_column(String(256))

    seller_sku: Mapped[str | None] = mapped_column(String(128), index=True)
    tts_product_id: Mapped[str | None] = mapped_column(String(64), index=True)
    tts_sku_id: Mapped[str | None] = mapped_column(String(64), index=True)

    face_value: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="面额（美分），与 codes.face_value 对齐"
    )
    safety_stock_threshold: Mapped[int | None] = mapped_column(
        Integer, comment="SKU 级安全库存阈值；为空时回退全局配置 stock_alert_threshold"
    )

    active: Mapped[bool] = mapped_column(default=True, nullable=False)

    list_price_cents: Mapped[int | None] = mapped_column(
        Integer, comment="TT 挂牌售价（美分/分）"
    )
    inventory_locked: Mapped[bool] = mapped_column(
        default=False, nullable=False, comment="调价倒挂锁库存"
    )
    inventory_lock_reason: Mapped[str | None] = mapped_column(
        Text, comment="锁库存原因"
    )
    inventory_locked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="锁库存时间"
    )
    wgcards_sku_id: Mapped[str | None] = mapped_column(
        String(64), comment="WGCards SKU ID"
    )
    wgcards_supplier_id: Mapped[int | None] = mapped_column(
        ForeignKey("suppliers.id"), comment="WGCards 供货商"
    )
    wgcards_face_value: Mapped[float | None] = mapped_column(
        Float, comment="WGCards 下单面值"
    )
    wgcards_auto_fulfill: Mapped[bool] = mapped_column(
        default=False,
        nullable=False,
        comment="已废弃，请用 supplier_sku_links.auto_fulfill",
    )
    supplier_links: Mapped[list["SupplierSkuLink"]] = relationship(  # noqa: F821
        back_populates="sku_mapping",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint(
            "seller_sku",
            "tts_product_id",
            "tts_sku_id",
            name="uq_sku_mapping_tts_triplet",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<SkuMapping internal={self.internal_sku_id} "
            f"seller_sku={self.seller_sku!r} face={self.face_value}>"
        )
