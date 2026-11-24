"""TT Shop 商品同步快照。

只读保存 TT 已上架商品和 SKU，用于后台把 TT SKU 匹配到内部库存 SKU。
履约仍以 sku_mapping 为准，这两张表只是降低手填 TT 字段的出错率。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class TTSProduct(Base, TimestampMixin):
    __tablename__ = "tts_products"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    shop_id: Mapped[str] = mapped_column(
        ForeignKey("tts_shops.shop_id"), nullable=False, index=True
    )
    tts_product_id: Mapped[str] = mapped_column(String(64), nullable=False)
    product_name: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str | None] = mapped_column(String(64), index=True)
    raw_payload: Mapped[dict | None] = mapped_column(JSONB)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    skus: Mapped[list["TTSProductSku"]] = relationship(  # noqa: F821
        back_populates="product",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("shop_id", "tts_product_id", name="uq_tts_products_shop_product"),
    )


class TTSProductSku(Base, TimestampMixin):
    __tablename__ = "tts_product_skus"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("tts_products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    shop_id: Mapped[str] = mapped_column(
        ForeignKey("tts_shops.shop_id"), nullable=False, index=True
    )
    tts_product_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    tts_sku_id: Mapped[str] = mapped_column(String(64), nullable=False)
    seller_sku: Mapped[str | None] = mapped_column(String(128), index=True)
    sku_name: Mapped[str | None] = mapped_column(String(512))
    currency: Mapped[str | None] = mapped_column(String(8))
    price_cents: Mapped[int | None] = mapped_column(Integer)
    inventory_quantity: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(String(64), index=True)
    raw_payload: Mapped[dict | None] = mapped_column(JSONB)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    match_status: Mapped[str | None] = mapped_column(
        String(32), index=True, comment="derived: matched / unmatched / conflict"
    )
    match_note: Mapped[str | None] = mapped_column(Text)

    product: Mapped[TTSProduct] = relationship(back_populates="skus")

    __table_args__ = (
        UniqueConstraint("shop_id", "tts_sku_id", name="uq_tts_product_skus_shop_sku"),
    )
