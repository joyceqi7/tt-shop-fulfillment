"""TT 商品同步快照表

Revision ID: 0010
Revises: 0005
Create Date: 2026-06-17
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0010"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    if not inspector.has_table("tts_products"):
        op.create_table(
            "tts_products",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("shop_id", sa.String(length=64), nullable=False),
            sa.Column("tts_product_id", sa.String(length=64), nullable=False),
            sa.Column("product_name", sa.String(length=512), nullable=True),
            sa.Column("status", sa.String(length=64), nullable=True),
            sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["shop_id"], ["tts_shops.shop_id"], name=op.f("fk_tts_products_shop_id_tts_shops")),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_tts_products")),
            sa.UniqueConstraint("shop_id", "tts_product_id", name="uq_tts_products_shop_product"),
        )
        op.create_index(op.f("ix_tts_products_shop_id"), "tts_products", ["shop_id"], unique=False)
        op.create_index(op.f("ix_tts_products_status"), "tts_products", ["status"], unique=False)

    if not inspector.has_table("tts_product_skus"):
        op.create_table(
            "tts_product_skus",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("product_id", sa.BigInteger(), nullable=False),
            sa.Column("shop_id", sa.String(length=64), nullable=False),
            sa.Column("tts_product_id", sa.String(length=64), nullable=False),
            sa.Column("tts_sku_id", sa.String(length=64), nullable=False),
            sa.Column("seller_sku", sa.String(length=128), nullable=True),
            sa.Column("sku_name", sa.String(length=512), nullable=True),
            sa.Column("currency", sa.String(length=8), nullable=True),
            sa.Column("price_cents", sa.Integer(), nullable=True),
            sa.Column("inventory_quantity", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(length=64), nullable=True),
            sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("match_status", sa.String(length=32), nullable=True, comment="derived: matched / unmatched / conflict"),
            sa.Column("match_note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["product_id"], ["tts_products.id"], name=op.f("fk_tts_product_skus_product_id_tts_products"), ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["shop_id"], ["tts_shops.shop_id"], name=op.f("fk_tts_product_skus_shop_id_tts_shops")),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_tts_product_skus")),
            sa.UniqueConstraint("shop_id", "tts_sku_id", name="uq_tts_product_skus_shop_sku"),
        )
        op.create_index(op.f("ix_tts_product_skus_product_id"), "tts_product_skus", ["product_id"], unique=False)
        op.create_index(op.f("ix_tts_product_skus_shop_id"), "tts_product_skus", ["shop_id"], unique=False)
        op.create_index(op.f("ix_tts_product_skus_tts_product_id"), "tts_product_skus", ["tts_product_id"], unique=False)
        op.create_index(op.f("ix_tts_product_skus_seller_sku"), "tts_product_skus", ["seller_sku"], unique=False)
        op.create_index(op.f("ix_tts_product_skus_status"), "tts_product_skus", ["status"], unique=False)
        op.create_index(op.f("ix_tts_product_skus_match_status"), "tts_product_skus", ["match_status"], unique=False)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("tts_product_skus"):
        op.drop_index(op.f("ix_tts_product_skus_match_status"), table_name="tts_product_skus")
        op.drop_index(op.f("ix_tts_product_skus_status"), table_name="tts_product_skus")
        op.drop_index(op.f("ix_tts_product_skus_seller_sku"), table_name="tts_product_skus")
        op.drop_index(op.f("ix_tts_product_skus_tts_product_id"), table_name="tts_product_skus")
        op.drop_index(op.f("ix_tts_product_skus_shop_id"), table_name="tts_product_skus")
        op.drop_index(op.f("ix_tts_product_skus_product_id"), table_name="tts_product_skus")
        op.drop_table("tts_product_skus")
    if inspector.has_table("tts_products"):
        op.drop_index(op.f("ix_tts_products_status"), table_name="tts_products")
        op.drop_index(op.f("ix_tts_products_shop_id"), table_name="tts_products")
        op.drop_table("tts_products")
