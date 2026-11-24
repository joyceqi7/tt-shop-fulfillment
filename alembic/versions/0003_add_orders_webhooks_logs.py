"""add orders, order_lines, webhook_events, fulfillment_log

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-22

注意：本 migration 不能动 0002 加的部分索引 ix_codes_pick
       （autogenerate 看不见它会想删，已手工剔除）
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fulfillment_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.String(length=64), nullable=True),
        sa.Column("order_line_id", sa.String(length=64), nullable=True),
        sa.Column("shop_id", sa.String(length=64), nullable=True),
        sa.Column("api_endpoint", sa.String(length=256), nullable=False),
        sa.Column("api_method", sa.String(length=8), nullable=False),
        sa.Column("request_params", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("request_body", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("response_body", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("api_code", sa.Integer(), nullable=True),
        sa.Column("api_message", sa.Text(), nullable=True),
        sa.Column(
            "request_id",
            sa.String(length=128),
            nullable=True,
            comment="TT 返回的 request_id，便于找客服",
        ),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "error",
            sa.Text(),
            nullable=True,
            comment="客户端异常信息（网络错等本地错误）",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fulfillment_log")),
    )
    op.create_index(op.f("ix_fulfillment_log_api_code"), "fulfillment_log", ["api_code"])
    op.create_index(op.f("ix_fulfillment_log_http_status"), "fulfillment_log", ["http_status"])
    op.create_index(op.f("ix_fulfillment_log_order_id"), "fulfillment_log", ["order_id"])
    op.create_index(op.f("ix_fulfillment_log_order_line_id"), "fulfillment_log", ["order_line_id"])
    op.create_index(op.f("ix_fulfillment_log_request_id"), "fulfillment_log", ["request_id"])
    op.create_index(op.f("ix_fulfillment_log_shop_id"), "fulfillment_log", ["shop_id"])

    op.create_table(
        "webhook_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("dedup_key", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=True),
        sa.Column("tts_notification_id", sa.String(length=128), nullable=True),
        sa.Column("shop_id", sa.String(length=64), nullable=True),
        sa.Column("order_id", sa.String(length=64), nullable=True),
        sa.Column("timestamp_tt", sa.BigInteger(), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("raw_headers", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("signature", sa.String(length=256), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_events")),
    )
    op.create_index(
        "ix_webhook_events_dedup_key",
        "webhook_events",
        ["dedup_key"],
        unique=True,
    )
    op.create_index(op.f("ix_webhook_events_event_type"), "webhook_events", ["event_type"])
    op.create_index(op.f("ix_webhook_events_order_id"), "webhook_events", ["order_id"])
    op.create_index(op.f("ix_webhook_events_shop_id"), "webhook_events", ["shop_id"])

    op.create_table(
        "tts_orders",
        sa.Column("order_id", sa.String(length=64), nullable=False),
        sa.Column("shop_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "buyer_email_encrypted",
            sa.LargeBinary(),
            nullable=True,
            comment="PII，Fernet 加密后落库",
        ),
        sa.Column("order_line_count", sa.Integer(), nullable=False),
        sa.Column("total_amount", sa.BigInteger(), nullable=True, comment="金额最小单位（美分）"),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("create_time_tt", sa.BigInteger(), nullable=True),
        sa.Column("update_time_tt", sa.BigInteger(), nullable=True),
        sa.Column("sla_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["shop_id"], ["tts_shops.shop_id"], name=op.f("fk_tts_orders_shop_id_tts_shops")
        ),
        sa.PrimaryKeyConstraint("order_id", name=op.f("pk_tts_orders")),
    )
    op.create_index(op.f("ix_tts_orders_shop_id"), "tts_orders", ["shop_id"])
    op.create_index(op.f("ix_tts_orders_status"), "tts_orders", ["status"])
    op.create_index(op.f("ix_tts_orders_update_time_tt"), "tts_orders", ["update_time_tt"])

    op.create_table(
        "tts_order_lines",
        sa.Column("order_line_id", sa.String(length=64), nullable=False),
        sa.Column("order_id", sa.String(length=64), nullable=False),
        sa.Column("seller_sku", sa.String(length=128), nullable=True),
        sa.Column("tts_product_id", sa.String(length=64), nullable=True),
        sa.Column("tts_sku_id", sa.String(length=64), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("redeemed_code_id", sa.BigInteger(), nullable=True),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_reason", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["order_id"], ["tts_orders.order_id"], name=op.f("fk_tts_order_lines_order_id_tts_orders")
        ),
        sa.ForeignKeyConstraint(
            ["redeemed_code_id"], ["codes.id"], name=op.f("fk_tts_order_lines_redeemed_code_id_codes")
        ),
        sa.PrimaryKeyConstraint("order_line_id", name=op.f("pk_tts_order_lines")),
    )
    op.create_index(op.f("ix_tts_order_lines_order_id"), "tts_order_lines", ["order_id"])
    op.create_index(op.f("ix_tts_order_lines_redeemed_code_id"), "tts_order_lines", ["redeemed_code_id"])
    op.create_index(op.f("ix_tts_order_lines_seller_sku"), "tts_order_lines", ["seller_sku"])


def downgrade() -> None:
    op.drop_index(op.f("ix_tts_order_lines_seller_sku"), table_name="tts_order_lines")
    op.drop_index(op.f("ix_tts_order_lines_redeemed_code_id"), table_name="tts_order_lines")
    op.drop_index(op.f("ix_tts_order_lines_order_id"), table_name="tts_order_lines")
    op.drop_table("tts_order_lines")

    op.drop_index(op.f("ix_tts_orders_update_time_tt"), table_name="tts_orders")
    op.drop_index(op.f("ix_tts_orders_status"), table_name="tts_orders")
    op.drop_index(op.f("ix_tts_orders_shop_id"), table_name="tts_orders")
    op.drop_table("tts_orders")

    op.drop_index(op.f("ix_webhook_events_shop_id"), table_name="webhook_events")
    op.drop_index(op.f("ix_webhook_events_order_id"), table_name="webhook_events")
    op.drop_index(op.f("ix_webhook_events_event_type"), table_name="webhook_events")
    op.drop_index("ix_webhook_events_dedup_key", table_name="webhook_events")
    op.drop_table("webhook_events")

    op.drop_index(op.f("ix_fulfillment_log_shop_id"), table_name="fulfillment_log")
    op.drop_index(op.f("ix_fulfillment_log_request_id"), table_name="fulfillment_log")
    op.drop_index(op.f("ix_fulfillment_log_order_line_id"), table_name="fulfillment_log")
    op.drop_index(op.f("ix_fulfillment_log_order_id"), table_name="fulfillment_log")
    op.drop_index(op.f("ix_fulfillment_log_http_status"), table_name="fulfillment_log")
    op.drop_index(op.f("ix_fulfillment_log_api_code"), table_name="fulfillment_log")
    op.drop_table("fulfillment_log")
