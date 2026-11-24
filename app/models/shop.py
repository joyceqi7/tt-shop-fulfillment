"""授权店铺表。Week 0 PoC 发现每次业务 API 都要 shop_cipher，必须先缓存。"""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class TTSShop(Base, TimestampMixin):
    __tablename__ = "tts_shops"

    shop_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    shop_cipher: Mapped[str] = mapped_column(String(128), nullable=False)
    shop_name: Mapped[str | None] = mapped_column(String(256))
    region: Mapped[str | None] = mapped_column(String(8), index=True)
    seller_type: Mapped[str | None] = mapped_column(String(32))
    seller_base_region: Mapped[str | None] = mapped_column(String(8))

    def __repr__(self) -> str:
        return f"<TTSShop id={self.shop_id} name={self.shop_name!r} region={self.region}>"
