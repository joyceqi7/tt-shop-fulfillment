"""SKU ↔ API 供应商外部 SKU 链接。"""

from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class SupplierSkuLink(Base, TimestampMixin):
    __tablename__ = "supplier_sku_links"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sku_mapping_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("sku_mapping.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    supplier_id: Mapped[int] = mapped_column(
        ForeignKey("suppliers.id"),
        nullable=False,
        index=True,
    )
    external_sku_id: Mapped[str] = mapped_column(String(64), nullable=False)
    external_face_value: Mapped[float | None] = mapped_column(
        Float, comment="供应商侧面值；空则用 internal face_value/100"
    )
    auto_fulfill: Mapped[bool] = mapped_column(
        default=False,
        nullable=False,
        comment="库存不足时自动向该供应商实时采购",
    )

    sku_mapping: Mapped["SkuMapping"] = relationship(back_populates="supplier_links")  # noqa: F821
    supplier: Mapped["Supplier"] = relationship(back_populates="sku_links")  # noqa: F821

    __table_args__ = (
        UniqueConstraint(
            "sku_mapping_id",
            "supplier_id",
            name="uq_supplier_sku_links_mapping_supplier",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<SupplierSkuLink id={self.id} sku_mapping={self.sku_mapping_id} "
            f"supplier={self.supplier_id} ext={self.external_sku_id!r}>"
        )
