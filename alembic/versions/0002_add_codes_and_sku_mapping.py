"""add codes and sku_mapping

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-22

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "codes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "batch_id",
            sa.UUID(as_uuid=False),
            nullable=False,
            comment="导入批次，便于召回/审计",
        ),
        sa.Column(
            "sku_id",
            sa.String(length=64),
            nullable=False,
            comment="内部 SKU ID，关联 sku_mapping.internal_sku_id",
        ),
        sa.Column("face_value", sa.Integer(), nullable=False, comment="面额（美分）"),
        sa.Column("code_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column(
            "code_hash",
            sa.String(length=64),
            nullable=False,
            comment="SHA-256 of plaintext, for dedup and lookup",
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "order_line_id",
            sa.String(length=64),
            nullable=True,
            comment="被哪个子单占用",
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "expired_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="卡密自身有效期，与订单无关",
        ),
        sa.Column(
            "operator",
            sa.String(length=64),
            nullable=True,
            comment="导入/操作人，审计",
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
            "status IN ('free', 'locked', 'used', 'void', 'expired', 'recycled')",
            name=op.f("ck_codes_code_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_codes")),
    )
    op.create_index("ix_codes_hash", "codes", ["code_hash"], unique=True)
    op.create_index(
        op.f("ix_codes_order_line_id"), "codes", ["order_line_id"], unique=False
    )
    op.create_index(op.f("ix_codes_status"), "codes", ["status"], unique=False)

    op.execute(
        """
        CREATE INDEX ix_codes_pick
        ON codes (sku_id, face_value, id)
        WHERE status = 'free'
        """
    )

    op.create_table(
        "sku_mapping",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("internal_sku_id", sa.String(length=64), nullable=False),
        sa.Column("seller_sku", sa.String(length=128), nullable=True),
        sa.Column("tts_product_id", sa.String(length=64), nullable=True),
        sa.Column("tts_sku_id", sa.String(length=64), nullable=True),
        sa.Column(
            "face_value",
            sa.Integer(),
            nullable=False,
            comment="面额（美分），与 codes.face_value 对齐",
        ),
        sa.Column("active", sa.Boolean(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sku_mapping")),
        sa.UniqueConstraint(
            "seller_sku",
            "tts_product_id",
            "tts_sku_id",
            name="uq_sku_mapping_tts_triplet",
        ),
    )
    op.create_index(
        op.f("ix_sku_mapping_internal_sku_id"),
        "sku_mapping",
        ["internal_sku_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_sku_mapping_seller_sku"), "sku_mapping", ["seller_sku"], unique=False
    )
    op.create_index(
        op.f("ix_sku_mapping_tts_product_id"),
        "sku_mapping",
        ["tts_product_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_sku_mapping_tts_sku_id"),
        "sku_mapping",
        ["tts_sku_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_sku_mapping_tts_sku_id"), table_name="sku_mapping")
    op.drop_index(op.f("ix_sku_mapping_tts_product_id"), table_name="sku_mapping")
    op.drop_index(op.f("ix_sku_mapping_seller_sku"), table_name="sku_mapping")
    op.drop_index(op.f("ix_sku_mapping_internal_sku_id"), table_name="sku_mapping")
    op.drop_table("sku_mapping")

    op.execute("DROP INDEX IF EXISTS ix_codes_pick")
    op.drop_index(op.f("ix_codes_status"), table_name="codes")
    op.drop_index(op.f("ix_codes_order_line_id"), table_name="codes")
    op.drop_index("ix_codes_hash", table_name="codes")
    op.drop_table("codes")
