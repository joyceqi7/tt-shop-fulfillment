"""restore revision marker for production databases

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-24
"""

from __future__ import annotations

from typing import Sequence, Union


revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op compatibility marker.

    Production previously recorded revision 0011 from a local deployment. The
    current Git history restores the TT product sync schema at 0010 and needs
    this marker so Alembic can recognize existing databases already stamped at
    0011.
    """


def downgrade() -> None:
    """No-op compatibility marker."""
