"""管理后台敏感操作审计日志。

每次执行对业务数据有副作用的动作（登录、reship、作废码、解锁码、导入批次、改 SKU、付款 等）
都会落一行。即便是 solo 单用户使用，也保留可追溯的操作历史，将来加多人协作零成本接入。

设计取舍：
- `target_type` + `target_id` 用文本字段而非外键 —— 因为 target 可能跨多张表（order / code / sku / purchase / supplier），
  外键没法多态；查询性能不是瓶颈，文本足够。
- `details` 字段名故意不叫 `metadata`：SQLAlchemy DeclarativeBase 已经占了 metadata 这个属性名，避而远之。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    operator: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="谁触发的（admin 用户名 / system）"
    )
    action: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="login / logout / reship / void / unlock / import / "
        "sku_create / sku_update / sku_delete / supplier_create / "
        "supplier_update / purchase_create / purchase_pay",
    )
    target_type: Mapped[str | None] = mapped_column(
        String(32), comment="order / code / batch / sku / purchase / supplier"
    )
    target_id: Mapped[str | None] = mapped_column(
        String(128), comment="业务主键，文本兼容多类型（order_id / code.id / sku.id 等）"
    )
    details: Mapped[dict | None] = mapped_column(
        JSONB, comment="入参快照、IP、user_agent 等",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_admin_audit_log_operator_created", "operator", "created_at"),
        Index("ix_admin_audit_log_target", "target_type", "target_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<AdminAuditLog op={self.operator} action={self.action} "
            f"target={self.target_type}:{self.target_id}>"
        )
