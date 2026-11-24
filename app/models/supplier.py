"""供货商表。

每个 supplier 是一个采购卡密的源头（如腾讯/阿里巴巴/Apple/某代理商），
可以挂多张 `purchase_orders`，每张采购单再关联一批 `codes`。

`enabled=False` 时不能新建采购单，但历史采购单仍可查询/付款。
"""

from __future__ import annotations

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Supplier(Base, TimestampMixin):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    contact_person: Mapped[str | None] = mapped_column(String(64))
    contact_phone: Mapped[str | None] = mapped_column(String(32))
    default_payment_terms_days: Mapped[int] = mapped_column(
        Integer, default=30, nullable=False, comment="默认账期（天），新建采购单时预填"
    )
    note: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)

    supplier_type: Mapped[str] = mapped_column(
        String(16),
        default="manual",
        nullable=False,
        comment="manual=线下采购, api=API对接",
    )
    adapter_key: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        comment="内部 adapter 标识，如 wgcards；Admin 不展示",
    )
    api_host: Mapped[str | None] = mapped_column(
        String(256),
        nullable=True,
        comment="API Host",
    )
    api_app_id: Mapped[str | None] = mapped_column(
        String(64),
        comment="WGCards App ID",
    )
    api_account_id: Mapped[str | None] = mapped_column(
        String(64),
        comment="WGCards Account ID",
    )
    api_app_key: Mapped[str | None] = mapped_column(
        String(128),
        comment="WGCards App Key",
    )
    api_currency: Mapped[str | None] = mapped_column(
        String(8),
        default="USD",
        comment="默认支付币种",
    )

    purchase_orders: Mapped[list["PurchaseOrder"]] = relationship(  # noqa: F821
        back_populates="supplier",
        cascade="save-update, merge",
    )
    supplier_orders: Mapped[list["SupplierOrder"]] = relationship(  # noqa: F821
        back_populates="supplier",
        cascade="save-update, merge",
    )
    sku_links: Mapped[list["SupplierSkuLink"]] = relationship(  # noqa: F821
        back_populates="supplier",
        cascade="save-update, merge",
    )

    def __repr__(self) -> str:
        return f"<Supplier id={self.id} name={self.name!r} enabled={self.enabled}>"

    @property
    def is_api(self) -> bool:
        return self.supplier_type == "api"

    @property
    def is_wgcards(self) -> bool:
        return self.is_api and bool(
            self.api_app_id and self.api_account_id and self.api_app_key
        )
