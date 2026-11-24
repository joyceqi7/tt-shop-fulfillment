"""API 供应商 SKU 调价倒挂预警。

对 auto_fulfill=true 且仍在售的 SKU 映射：
净收入 = list_price_cents × (1 - tt_platform_commission_rate)
若净收入 < WGCards 实时采购价 → 锁库存 + 飞书告警。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.models.sku_mapping import SkuMapping
from app.models.supplier import Supplier
from app.models.supplier_sku_link import SupplierSkuLink
from app.services.alerts import send_feishu_text
from app.services.supplier_adapters.registry import get_adapter
from app.services.wgcards_client import WGCardsAPIError
from app.services.supplier_adapters.wgcards import build_wgcards_client

logger = logging.getLogger(__name__)

_ALERT_TTL_SECONDS = 86400  # 24h 去重


def _money(cents: int, currency: str) -> str:
    return f"{cents / 100:.2f} {currency}"


def _sku_price_to_cents(sku_price: float | int | None) -> int | None:
    if sku_price is None:
        return None
    return round(float(sku_price) * 100)


async def _should_alert(redis: Redis, sku_mapping_id: int) -> bool:
    key = f"price_guard:alert:{sku_mapping_id}"
    ok = await redis.set(key, "1", nx=True, ex=_ALERT_TTL_SECONDS)
    return bool(ok)


async def _clear_alert_dedup(redis: Redis, sku_mapping_id: int) -> None:
    await redis.delete(f"price_guard:alert:{sku_mapping_id}")


async def run_price_guard(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    redis: Redis,
    settings: Settings | None = None,
) -> dict[str, int]:
    """执行一轮比价；返回统计。"""
    settings = settings or get_settings()
    commission = settings.tt_platform_commission_rate
    stats = {"checked": 0, "locked": 0, "unlocked": 0, "skipped": 0, "errors": 0}

    async with session_factory() as db:
        rows = (
            await db.execute(
                select(SupplierSkuLink, SkuMapping, Supplier)
                .join(SkuMapping, SupplierSkuLink.sku_mapping_id == SkuMapping.id)
                .join(Supplier, SupplierSkuLink.supplier_id == Supplier.id)
                .where(
                    SupplierSkuLink.auto_fulfill.is_(True),
                    SkuMapping.active.is_(True),
                    Supplier.enabled.is_(True),
                    Supplier.supplier_type == "api",
                )
            )
        ).all()

    by_supplier: dict[int, list[tuple[SupplierSkuLink, SkuMapping, Supplier]]] = {}
    for link, mapping, supplier in rows:
        if get_adapter(supplier) is None:
            stats["skipped"] += 1
            continue
        by_supplier.setdefault(supplier.id, []).append((link, mapping, supplier))

    for supplier_id, group in by_supplier.items():
        supplier = group[0][2]
        currency = supplier.api_currency or "CNY"
        try:
            client = build_wgcards_client(supplier)
        except ValueError as e:
            logger.warning("price_guard skip supplier=%s: %s", supplier_id, e)
            stats["skipped"] += len(group)
            continue

        for link, mapping, _ in group:
            stats["checked"] += 1
            if mapping.list_price_cents is None:
                stats["skipped"] += 1
                continue

            try:
                catalog = await client.lookup_sku(
                    link.external_sku_id,
                    currency_code=currency,
                )
            except WGCardsAPIError as e:
                logger.warning(
                    "price_guard lookup failed mapping=%s sku=%s: %s",
                    mapping.id,
                    link.external_sku_id,
                    e,
                )
                stats["errors"] += 1
                continue

            cost_cents = _sku_price_to_cents(
                catalog.get("skuPrice") if catalog else None
            )
            if cost_cents is None:
                stats["skipped"] += 1
                continue

            net_cents = round(mapping.list_price_cents * (1 - commission))
            inverted = net_cents < cost_cents

            async with session_factory() as db:
                db_mapping = await db.get(SkuMapping, mapping.id)
                if db_mapping is None:
                    continue

                if inverted:
                    reason = (
                        f"扣税净价 {_money(net_cents, currency)} "
                        f"< WGCards 实时采购价 {_money(cost_cents, currency)}"
                    )
                    was_locked = db_mapping.inventory_locked
                    db_mapping.inventory_locked = True
                    db_mapping.inventory_lock_reason = reason
                    db_mapping.inventory_locked_at = datetime.now(UTC)
                    await db.commit()
                    stats["locked"] += int(not was_locked)

                    if not was_locked and await _should_alert(redis, mapping.id):
                        product = db_mapping.product_name or db_mapping.internal_sku_id
                        text = (
                            "【API 调价预警】SKU 已锁库存\n"
                            f"商品：{product}\n"
                            f"internal_sku_id：{db_mapping.internal_sku_id}\n"
                            f"原因：{reason}"
                        )
                        await send_feishu_text(text)
                elif db_mapping.inventory_locked:
                    db_mapping.inventory_locked = False
                    db_mapping.inventory_lock_reason = None
                    db_mapping.inventory_locked_at = None
                    await db.commit()
                    await _clear_alert_dedup(redis, mapping.id)
                    stats["unlocked"] += 1

    logger.info("price_guard done: %s", stats)
    return stats
