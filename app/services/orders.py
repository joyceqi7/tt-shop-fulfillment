"""订单 upsert：把 TT `get_order_detail` 返回的 order 对象写入本地表。

TT 202507 订单详情接口返回字段（按 Partner Center 文档 + PoC 经验汇总）：

```
data.orders[].id                     → tts_orders.order_id
data.orders[].status                 → tts_orders.status
data.orders[].create_time            → create_time_tt (unix)
data.orders[].update_time            → update_time_tt (unix)
data.orders[].payment.total_amount   → total_amount（字符串金额，换算美分）
data.orders[].payment.currency       → currency
data.orders[].line_items[].id        → tts_order_lines.order_line_id
data.orders[].line_items[].seller_sku
data.orders[].line_items[].product_id
data.orders[].line_items[].sku_id
data.orders[].line_items[].quantity
data.orders[].line_items[].sale_price（字符串金额）
```

⚠️ 真实字段名以联调第一个真实订单为准；这里给默认解析路径 + 兜底。
upsert 策略：
- 订单主表：覆盖 status / amount / update_time / raw_payload
- 订单行表：覆盖 SKU/价格/数量；**绝不覆盖** status / redeemed_code_id / redeemed_at / failed_reason
  （这些是履约状态，属于本地权威数据，TT 再推也不能回滚）
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.order import TTSOrder, TTSOrderLine


logger = logging.getLogger(__name__)


def _to_cents(val: Any) -> int | None:
    """TT 金额通常是字符串 '19.99' 或 number。转成美分。"""
    if val is None:
        return None
    try:
        return int(round(float(val) * 100))
    except (TypeError, ValueError):
        return None


def _str_or_none(val: Any) -> str | None:
    if val is None:
        return None
    s = str(val)
    return s if s else None


async def upsert_order_from_tt(
    session: AsyncSession,
    *,
    order: dict,
    shop_id: str,
) -> tuple[TTSOrder, list[TTSOrderLine]]:
    """对 TT 单条订单对象做 upsert，返回最新 ORM 对象 + line 列表。"""

    order_id = _str_or_none(order.get("id") or order.get("order_id"))
    if not order_id:
        raise ValueError(f"order payload missing id: {order!r}")

    status = order.get("status") or order.get("order_status") or "UNKNOWN"
    line_items = order.get("line_items") or order.get("order_line_items") or []

    payment = order.get("payment") if isinstance(order.get("payment"), dict) else {}

    order_values = {
        "order_id": order_id,
        "shop_id": shop_id,
        "status": status,
        "order_line_count": len(line_items),
        "total_amount": _to_cents(payment.get("total_amount")),
        "currency": payment.get("currency"),
        "create_time_tt": order.get("create_time"),
        "update_time_tt": order.get("update_time"),
        "raw_payload": order,
    }

    ord_stmt = (
        pg_insert(TTSOrder)
        .values(**order_values)
        .on_conflict_do_update(
            index_elements=["order_id"],
            set_={
                "status": order_values["status"],
                "order_line_count": order_values["order_line_count"],
                "total_amount": order_values["total_amount"],
                "currency": order_values["currency"],
                "update_time_tt": order_values["update_time_tt"],
                "raw_payload": order_values["raw_payload"],
                "updated_at": func.now(),
            },
        )
    )
    await session.execute(ord_stmt)

    for li in line_items:
        line_id = _str_or_none(li.get("id") or li.get("order_line_id"))
        if not line_id:
            logger.warning("order %s has a line without id: %r", order_id, li)
            continue

        line_values = {
            "order_line_id": line_id,
            "order_id": order_id,
            "seller_sku": li.get("seller_sku"),
            "tts_product_id": _str_or_none(li.get("product_id")),
            "tts_sku_id": _str_or_none(li.get("sku_id")),
            "quantity": int(li.get("quantity") or 1),
            "unit_price": _to_cents(li.get("sale_price") or li.get("unit_price")),
            "currency": li.get("currency") or payment.get("currency"),
            "status": "pending",
        }
        line_stmt = (
            pg_insert(TTSOrderLine)
            .values(**line_values)
            .on_conflict_do_update(
                index_elements=["order_line_id"],
                set_={
                    "seller_sku": line_values["seller_sku"],
                    "tts_product_id": line_values["tts_product_id"],
                    "tts_sku_id": line_values["tts_sku_id"],
                    "quantity": line_values["quantity"],
                    "unit_price": line_values["unit_price"],
                    "currency": line_values["currency"],
                    "updated_at": func.now(),
                },
            )
        )
        await session.execute(line_stmt)

    await session.flush()

    result = await session.execute(
        select(TTSOrder)
        .where(TTSOrder.order_id == order_id)
        .options(selectinload(TTSOrder.lines))
    )
    tts_order = result.scalar_one()
    return tts_order, list(tts_order.lines)
