"""admin console schema: audit log + suppliers + purchase_orders + codes.purchase_order_id

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-27

为后台管理控制台（admin.sailson.com）一次性补齐数据层：
- admin_audit_log    敏感操作审计
- suppliers          供货商
- purchase_orders    采购单（含付款状态）
- codes              加 purchase_order_id 外键，把卡密成本链路打通

历史 codes（4-23 cutover 之前）`purchase_order_id` 为 NULL，
财务模块以"无成本数据"标记，可后续通过"批量补录到一个虚拟历史 PO"工具一次性归档。
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- admin_audit_log ----
    op.create_table(
        "admin_audit_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "operator",
            sa.String(length=64),
            nullable=False,
            comment="谁触发的（admin 用户名 / system）",
        ),
        sa.Column(
            "action",
            sa.String(length=32),
            nullable=False,
            comment="login / reship / void / unlock / import / sku_* / supplier_* / purchase_*",
        ),
        sa.Column(
            "target_type",
            sa.String(length=32),
            nullable=True,
            comment="order / code / batch / sku / purchase / supplier",
        ),
        sa.Column(
            "target_id",
            sa.String(length=128),
            nullable=True,
            comment="业务主键，文本兼容多类型",
        ),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="入参快照、IP、user_agent 等",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_audit_log")),
    )
    op.create_index(
        "ix_admin_audit_log_operator_created",
        "admin_audit_log",
        ["operator", "created_at"],
    )
    op.create_index(
        "ix_admin_audit_log_target",
        "admin_audit_log",
        ["target_type", "target_id"],
    )

    # ---- suppliers ----
    op.create_table(
        "suppliers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("contact_person", sa.String(length=64), nullable=True),
        sa.Column("contact_phone", sa.String(length=32), nullable=True),
        sa.Column(
            "default_payment_terms_days",
            sa.Integer(),
            server_default=sa.text("30"),
            nullable=False,
            comment="默认账期（天），新建采购单时预填",
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_suppliers")),
        sa.UniqueConstraint("name", name=op.f("uq_suppliers_name")),
    )

    # ---- purchase_orders ----
    op.create_table(
        "purchase_orders",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "po_number",
            sa.String(length=64),
            nullable=False,
            comment="人工编号或自动生成 PO20260427-001",
        ),
        sa.Column("supplier_id", sa.Integer(), nullable=False),
        sa.Column(
            "sku_id",
            sa.String(length=64),
            nullable=False,
            comment="关联 sku_mapping.internal_sku_id",
        ),
        sa.Column(
            "face_value",
            sa.Integer(),
            nullable=False,
            comment="面额（美分）",
        ),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column(
            "unit_cost",
            sa.Integer(),
            nullable=False,
            comment="单价（美分）",
        ),
        sa.Column(
            "total_cost",
            sa.BigInteger(),
            nullable=False,
            comment="冗余：quantity × unit_cost，便于聚合查询不重算",
        ),
        sa.Column("purchase_date", sa.Date(), nullable=False),
        sa.Column("payment_due_date", sa.Date(), nullable=True),
        sa.Column(
            "amount_paid",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "payment_status",
            sa.String(length=16),
            server_default=sa.text("'unpaid'"),
            nullable=False,
        ),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "batch_id",
            postgresql.UUID(as_uuid=False),
            nullable=True,
            comment="对应 codes.batch_id（导入完成后填）",
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "payment_status IN ('unpaid', 'partial', 'paid', 'overdue')",
            name=op.f("ck_purchase_orders_payment_status"),
        ),
        sa.ForeignKeyConstraint(
            ["supplier_id"],
            ["suppliers.id"],
            name=op.f("fk_purchase_orders_supplier_id_suppliers"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_orders")),
        sa.UniqueConstraint("po_number", name=op.f("uq_purchase_orders_po_number")),
    )
    op.create_index(
        op.f("ix_purchase_orders_supplier_id"),
        "purchase_orders",
        ["supplier_id"],
    )
    op.create_index(
        op.f("ix_purchase_orders_payment_due_date"),
        "purchase_orders",
        ["payment_due_date"],
    )
    op.create_index(
        op.f("ix_purchase_orders_payment_status"),
        "purchase_orders",
        ["payment_status"],
    )
    op.create_index(
        "ix_purchase_orders_supplier_date",
        "purchase_orders",
        ["supplier_id", "purchase_date"],
    )
    # 部分索引：只索引未付清的，加速 /finance/payable 查询
    op.create_index(
        "ix_purchase_orders_unpaid",
        "purchase_orders",
        ["payment_due_date"],
        postgresql_where=sa.text("payment_status != 'paid'"),
    )

    # ---- codes 加 purchase_order_id 外键 ----
    op.add_column(
        "codes",
        sa.Column(
            "purchase_order_id",
            sa.BigInteger(),
            nullable=True,
            comment="关联采购单（成本来源）；历史码为空",
        ),
    )
    op.create_foreign_key(
        op.f("fk_codes_purchase_order_id_purchase_orders"),
        "codes",
        "purchase_orders",
        ["purchase_order_id"],
        ["id"],
    )
    op.create_index(
        op.f("ix_codes_purchase_order_id"),
        "codes",
        ["purchase_order_id"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_codes_purchase_order_id"), table_name="codes")
    op.drop_constraint(
        op.f("fk_codes_purchase_order_id_purchase_orders"),
        "codes",
        type_="foreignkey",
    )
    op.drop_column("codes", "purchase_order_id")

    op.drop_index("ix_purchase_orders_unpaid", table_name="purchase_orders")
    op.drop_index("ix_purchase_orders_supplier_date", table_name="purchase_orders")
    op.drop_index(
        op.f("ix_purchase_orders_payment_status"), table_name="purchase_orders"
    )
    op.drop_index(
        op.f("ix_purchase_orders_payment_due_date"), table_name="purchase_orders"
    )
    op.drop_index(op.f("ix_purchase_orders_supplier_id"), table_name="purchase_orders")
    op.drop_table("purchase_orders")

    op.drop_table("suppliers")

    op.drop_index("ix_admin_audit_log_target", table_name="admin_audit_log")
    op.drop_index(
        "ix_admin_audit_log_operator_created", table_name="admin_audit_log"
    )
    op.drop_table("admin_audit_log")
