"""Access/Refresh token 持久化。

关键设计：
- access_token_expire_at 存 **Unix 绝对时间戳**（Week 0 PoC 确认 TT 返回的是绝对时间而非相对秒数）
- access_token_encrypted / refresh_token_encrypted 用 Fernet 加密后存 bytea
- version 字段做乐观锁，防 refresh 过程中多实例并发覆盖
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class TTSToken(Base, TimestampMixin):
    __tablename__ = "tts_tokens"

    app_key: Mapped[str] = mapped_column(String(64), primary_key=True)

    access_token_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    access_token_expire_at: Mapped[int] = mapped_column(
        BigInteger, nullable=False,
        comment="Unix absolute timestamp returned by TT (NOT remaining seconds)",
    )

    refresh_token_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    refresh_token_expire_at: Mapped[int | None] = mapped_column(BigInteger)

    seller_name: Mapped[str | None] = mapped_column(String(256))
    open_id: Mapped[str | None] = mapped_column(String(64))

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __mapper_args__ = {"version_id_col": version}

    def __repr__(self) -> str:
        return (
            f"<TTSToken app_key={self.app_key} "
            f"expire_at={self.access_token_expire_at} v={self.version}>"
        )
