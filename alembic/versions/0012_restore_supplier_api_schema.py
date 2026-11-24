"""restore supplier API procurement schema

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-24
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(inspector: sa.Inspector, table: str, column: str) -> bool:
    return column in {c["name"] for c in inspector.get_columns(table)}


def _has_index(inspector: sa.Inspector, table: str, index: str) -> bool:
    return index in {i["name"] for i in inspector.get_indexes(table)}


def _has_constraint(inspector: sa.Inspector, table: str, constraint: str) -> bool:
    return constraint in _constraint_names(inspector, table)


def _constraint_names(inspector: sa.Inspector, table: str) -> set[str]:
    names = {c["name"] for c in inspector.get_unique_constraints(table)}
    names.update(c["name"] for c in inspector.get_check_constraints(table))
    names.update(c["name"] for c in inspector.get_foreign_keys(table))
    return names


def _has_any_constraint(
    inspector: sa.Inspector,
    table: str,
    constraints: set[str],
) -> bool:
    return bool(_constraint_names(inspector, table) & constraints)


def _drop_existing_constraints(
    inspector: sa.Inspector,
    table: str,
    constraints: set[str],
) -> None:
    for name in _constraint_names(inspector, table) & constraints:
        op.drop_constraint(op.f(name), table, type_="check")


def _add_column_if_missing(
    inspector: sa.Inspector,
    table: str,
    column: sa.Column,
) -> None:
    if not _has_column(inspector, table, column.name):
        op.add_column(table, column)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("suppliers"):
        _add_column_if_missing(
            inspector,
            "suppliers",
            sa.Column(
                "api_host",
                sa.String(length=256),
                nullable=True,
                comment="API Host",
            ),
        )
        _add_column_if_missing(
            inspector,
            "suppliers",
            sa.Column(
                "api_app_id",
                sa.String(length=64),
                nullable=True,
                comment="WGCards App ID",
            ),
        )
        _add_column_if_missing(
            inspector,
            "suppliers",
            sa.Column(
                "api_account_id",
                sa.String(length=64),
                nullable=True,
                comment="WGCards Account ID",
            ),
        )
        _add_column_if_missing(
            inspector,
            "suppliers",
            sa.Column(
                "api_app_key",
                sa.String(length=128),
                nullable=True,
                comment="WGCards App Key",
            ),
        )
        _add_column_if_missing(
            inspector,
            "suppliers",
            sa.Column(
                "api_currency",
                sa.String(length=8),
                nullable=True,
                comment="默认支付币种",
            ),
        )
        _add_column_if_missing(
            inspector,
            "suppliers",
            sa.Column(
                "supplier_type",
                sa.String(length=16),
                server_default="manual",
                nullable=False,
                comment="manual=线下采购, api=API对接",
            ),
        )
        _add_column_if_missing(
            inspector,
            "suppliers",
            sa.Column(
                "adapter_key",
                sa.String(length=32),
                nullable=True,
                comment="内部 adapter 标识",
            ),
        )
        inspector = sa.inspect(bind)
        if not _has_any_constraint(
            inspector,
            "suppliers",
            {"ck_suppliers_supplier_type", "ck_suppliers_ck_suppliers_supplier_type"},
        ):
            op.create_check_constraint(
                op.f("ck_suppliers_supplier_type"),
                "suppliers",
                "supplier_type IN ('manual', 'api')",
            )
        if not _has_index(inspector, "suppliers", "ix_suppliers_supplier_type"):
            op.create_index(
                "ix_suppliers_supplier_type",
                "suppliers",
                ["supplier_type"],
            )
        op.execute(
            """
            UPDATE suppliers
            SET supplier_type = 'api', adapter_key = COALESCE(adapter_key, 'wgcards')
            WHERE api_app_id IS NOT NULL AND api_app_key IS NOT NULL
            """
        )

    if inspector.has_table("sku_mapping"):
        _add_column_if_missing(
            inspector,
            "sku_mapping",
            sa.Column(
                "wgcards_sku_id",
                sa.String(length=64),
                nullable=True,
                comment="WGCards SKU ID",
            ),
        )
        _add_column_if_missing(
            inspector,
            "sku_mapping",
            sa.Column(
                "wgcards_supplier_id",
                sa.Integer(),
                nullable=True,
                comment="WGCards 供货商 ID",
            ),
        )
        _add_column_if_missing(
            inspector,
            "sku_mapping",
            sa.Column(
                "wgcards_face_value",
                sa.Float(),
                nullable=True,
                comment="WGCards 下单面值",
            ),
        )
        _add_column_if_missing(
            inspector,
            "sku_mapping",
            sa.Column(
                "wgcards_auto_fulfill",
                sa.Boolean(),
                server_default=sa.text("false"),
                nullable=False,
                comment="已废弃，请用 supplier_sku_links.auto_fulfill",
            ),
        )
        _add_column_if_missing(
            inspector,
            "sku_mapping",
            sa.Column(
                "list_price_cents",
                sa.Integer(),
                nullable=True,
                comment="TT 挂牌售价（美分/分）",
            ),
        )
        _add_column_if_missing(
            inspector,
            "sku_mapping",
            sa.Column(
                "inventory_locked",
                sa.Boolean(),
                server_default=sa.text("false"),
                nullable=False,
                comment="调价倒挂锁库存",
            ),
        )
        _add_column_if_missing(
            inspector,
            "sku_mapping",
            sa.Column(
                "inventory_lock_reason",
                sa.Text(),
                nullable=True,
                comment="锁库存原因",
            ),
        )
        _add_column_if_missing(
            inspector,
            "sku_mapping",
            sa.Column(
                "inventory_locked_at",
                sa.DateTime(timezone=True),
                nullable=True,
                comment="锁库存时间",
            ),
        )
        inspector = sa.inspect(bind)
        if not _has_constraint(
            inspector,
            "sku_mapping",
            "fk_sku_mapping_wgcards_supplier_id",
        ):
            op.create_foreign_key(
                "fk_sku_mapping_wgcards_supplier_id",
                "sku_mapping",
                "suppliers",
                ["wgcards_supplier_id"],
                ["id"],
            )

    if not inspector.has_table("supplier_orders"):
        op.create_table(
            "supplier_orders",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("supplier_id", sa.Integer(), nullable=False),
            sa.Column("tt_order_id", sa.String(length=64), nullable=True),
            sa.Column("tt_order_line_id", sa.String(length=64), nullable=True),
            sa.Column("procurement_mode", sa.String(length=16), server_default="realtime", nullable=False),
            sa.Column("purchase_order_id", sa.Integer(), nullable=True),
            sa.Column("supplier_order_id", sa.String(length=64), nullable=True),
            sa.Column("service_order", sa.String(length=128), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
            sa.Column("sku_id", sa.String(length=64), nullable=False),
            sa.Column("sku_name", sa.String(length=256), nullable=True),
            sa.Column("face_value", sa.Float(), nullable=True),
            sa.Column("buy_num", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("currency", sa.String(length=8), nullable=False, server_default="USD"),
            sa.Column("total_amount", sa.Float(), nullable=True),
            sa.Column("card_codes", postgresql.ARRAY(sa.String(length=256)), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("raw_response", sa.Text(), nullable=True),
            sa.Column("placed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("card_ready_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("elapsed_seconds", sa.Float(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.CheckConstraint(
                "status IN ('pending', 'placed', 'processing', 'card_ready', 'imported', 'shipped', 'timeout', 'failed', 'cancelled')",
                name=op.f("ck_supplier_orders_supplier_order_status"),
            ),
            sa.CheckConstraint(
                "procurement_mode IN ('realtime', 'bulk')",
                name=op.f("ck_supplier_orders_supplier_order_procurement_mode"),
            ),
            sa.ForeignKeyConstraint(
                ["supplier_id"],
                ["suppliers.id"],
                name="fk_supplier_orders_supplier_id_suppliers",
            ),
            sa.ForeignKeyConstraint(
                ["purchase_order_id"],
                ["purchase_orders.id"],
                name="fk_supplier_orders_purchase_order_id",
            ),
            sa.PrimaryKeyConstraint("id", name="pk_supplier_orders"),
        )
    else:
        inspector = sa.inspect(bind)
        _add_column_if_missing(
            inspector,
            "supplier_orders",
            sa.Column("procurement_mode", sa.String(length=16), server_default="realtime", nullable=False),
        )
        _add_column_if_missing(
            inspector,
            "supplier_orders",
            sa.Column("purchase_order_id", sa.Integer(), nullable=True),
        )
        inspector = sa.inspect(bind)
        if not _has_constraint(
            inspector,
            "supplier_orders",
            "fk_supplier_orders_purchase_order_id",
        ):
            op.create_foreign_key(
                "fk_supplier_orders_purchase_order_id",
                "supplier_orders",
                "purchase_orders",
                ["purchase_order_id"],
                ["id"],
            )

    inspector = sa.inspect(bind)
    if inspector.has_table("supplier_orders"):
        _drop_existing_constraints(
            inspector,
            "supplier_orders",
            {
                "ck_supplier_orders_supplier_order_status",
                "ck_supplier_orders_ck_supplier_orders_supplier_order_status",
            },
        )
        op.create_check_constraint(
            op.f("ck_supplier_orders_supplier_order_status"),
            "supplier_orders",
            "status IN ('pending', 'placed', 'processing', 'card_ready', "
            "'imported', 'shipped', 'timeout', 'failed', 'cancelled')",
        )

        inspector = sa.inspect(bind)
        if not _has_any_constraint(
            inspector,
            "supplier_orders",
            {
                "ck_supplier_orders_supplier_order_procurement_mode",
                "ck_supplier_orders_procurement_mode",
                "ck_supplier_orders_ck_supplier_orders_supplier_order_procurement_mode",
                "ck_supplier_orders_ck_supplier_orders_procurement_mode",
            },
        ):
            op.create_check_constraint(
                op.f("ck_supplier_orders_supplier_order_procurement_mode"),
                "supplier_orders",
                "procurement_mode IN ('realtime', 'bulk')",
            )

        inspector = sa.inspect(bind)
        indexes = [
            ("ix_supplier_orders_service_order", ["service_order"], None),
            ("ix_supplier_orders_status", ["status"], None),
            ("ix_supplier_orders_supplier_id", ["supplier_id"], None),
            ("ix_supplier_orders_tt_line", ["tt_order_id", "tt_order_line_id"], None),
            ("ix_supplier_orders_tt_order_id", ["tt_order_id"], None),
            ("ix_supplier_orders_tt_order_line_id", ["tt_order_line_id"], None),
            ("ix_supplier_orders_procurement_mode", ["procurement_mode"], None),
            (
                "ix_supplier_orders_pending",
                ["status"],
                sa.text("status IN ('pending', 'placed', 'processing')"),
            ),
        ]
        for name, cols, where in indexes:
            if not _has_index(inspector, "supplier_orders", name):
                op.create_index(
                    name,
                    "supplier_orders",
                    cols,
                    postgresql_where=where,
                )

    inspector = sa.inspect(bind)
    if not inspector.has_table("supplier_sku_links"):
        op.create_table(
            "supplier_sku_links",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("sku_mapping_id", sa.BigInteger(), nullable=False),
            sa.Column("supplier_id", sa.Integer(), nullable=False),
            sa.Column("external_sku_id", sa.String(length=64), nullable=False),
            sa.Column("external_face_value", sa.Float(), nullable=True),
            sa.Column("auto_fulfill", sa.Boolean(), server_default=sa.text("false"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(
                ["sku_mapping_id"],
                ["sku_mapping.id"],
                name=op.f("fk_supplier_sku_links_sku_mapping_id_sku_mapping"),
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["supplier_id"],
                ["suppliers.id"],
                name=op.f("fk_supplier_sku_links_supplier_id_suppliers"),
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_supplier_sku_links")),
            sa.UniqueConstraint(
                "sku_mapping_id",
                "supplier_id",
                name="uq_supplier_sku_links_mapping_supplier",
            ),
        )
        op.create_index(
            "ix_supplier_sku_links_sku_mapping_id",
            "supplier_sku_links",
            ["sku_mapping_id"],
        )
        op.create_index(
            "ix_supplier_sku_links_supplier_id",
            "supplier_sku_links",
            ["supplier_id"],
        )
        if inspector.has_table("sku_mapping"):
            op.execute(
                """
                INSERT INTO supplier_sku_links (
                    sku_mapping_id, supplier_id, external_sku_id,
                    external_face_value, auto_fulfill, created_at, updated_at
                )
                SELECT
                    id, wgcards_supplier_id, wgcards_sku_id,
                    wgcards_face_value, wgcards_auto_fulfill, NOW(), NOW()
                FROM sku_mapping
                WHERE wgcards_supplier_id IS NOT NULL
                  AND wgcards_sku_id IS NOT NULL
                ON CONFLICT DO NOTHING
                """
            )


def downgrade() -> None:
    """Keep the compatibility schema in place on downgrade."""
