"""initial: tts_shops + tts_tokens

Revision ID: 0001
Revises:
Create Date: 2026-04-22

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tts_shops",
        sa.Column("shop_id", sa.String(length=64), nullable=False),
        sa.Column("shop_cipher", sa.String(length=128), nullable=False),
        sa.Column("shop_name", sa.String(length=256), nullable=True),
        sa.Column("region", sa.String(length=8), nullable=True),
        sa.Column("seller_type", sa.String(length=32), nullable=True),
        sa.Column("seller_base_region", sa.String(length=8), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("shop_id", name=op.f("pk_tts_shops")),
    )
    op.create_index(
        op.f("ix_tts_shops_region"), "tts_shops", ["region"], unique=False
    )

    op.create_table(
        "tts_tokens",
        sa.Column("app_key", sa.String(length=64), nullable=False),
        sa.Column("access_token_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column(
            "access_token_expire_at",
            sa.BigInteger(),
            nullable=False,
            comment="Unix absolute timestamp returned by TT (NOT remaining seconds)",
        ),
        sa.Column("refresh_token_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("refresh_token_expire_at", sa.BigInteger(), nullable=True),
        sa.Column("seller_name", sa.String(length=256), nullable=True),
        sa.Column("open_id", sa.String(length=64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("app_key", name=op.f("pk_tts_tokens")),
    )


def downgrade() -> None:
    op.drop_table("tts_tokens")
    op.drop_index(op.f("ix_tts_shops_region"), table_name="tts_shops")
    op.drop_table("tts_shops")
