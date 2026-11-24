"""sku mapping add product_name and safety_stock_threshold

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-29
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sku_mapping", sa.Column("product_name", sa.String(length=256), nullable=True))
    op.add_column(
        "sku_mapping",
        sa.Column(
            "safety_stock_threshold",
            sa.Integer(),
            nullable=True,
            comment="SKU 级安全库存阈值；为空时回退全局配置 stock_alert_threshold",
        ),
    )


def downgrade() -> None:
    op.drop_column("sku_mapping", "safety_stock_threshold")
    op.drop_column("sku_mapping", "product_name")

