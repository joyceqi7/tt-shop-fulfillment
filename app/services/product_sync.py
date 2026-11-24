"""TT 商品只读同步。

目标：把 TT 已上架商品/SKU 拉到本地快照表，供后台做“TT SKU → 内部库存 SKU”
匹配。此服务不调用任何 TT 商品写接口。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.shop import TTSShop
from app.models.sku_mapping import SkuMapping
from app.models.tts_product import TTSProduct, TTSProductSku
from app.services.tts_client import TTSClient


logger = logging.getLogger(__name__)


@dataclass
class ProductSyncShopResult:
    shop_id: str
    shop_name: str | None
    products_seen: int = 0
    skus_seen: int = 0
    pages_seen: int = 0
    error: str | None = None


@dataclass
class ProductSyncResult:
    shops_total: int = 0
    shops_synced: int = 0
    products_seen: int = 0
    skus_seen: int = 0
    shop_results: list[ProductSyncShopResult] = field(default_factory=list)


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _first_str(obj: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        v = _str_or_none(obj.get(key))
        if v:
            return v
    return None


def _nested(obj: dict[str, Any], *path: str) -> Any:
    cur: Any = obj
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _money_to_cents(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(round(float(value) * 100))
    except (TypeError, ValueError):
        return None


def _extract_price_cents(sku: dict[str, Any]) -> int | None:
    direct = _to_int(
        sku.get("price_cents")
        or sku.get("sale_price_cents")
        or sku.get("list_price_cents")
    )
    if direct is not None:
        return direct

    for container_key in ("price", "sale_price", "list_price", "retail_price"):
        raw = sku.get(container_key)
        if isinstance(raw, dict):
            cents = _to_int(
                raw.get("amount_in_cents")
                or raw.get("cent_amount")
                or raw.get("price_cents")
            )
            if cents is not None:
                return cents
            amount = raw.get("amount") or raw.get("value")
            cents = _money_to_cents(amount)
            if cents is not None:
                return cents
        else:
            cents = _money_to_cents(raw)
            if cents is not None:
                return cents
    return None


def _extract_currency(product: dict[str, Any], sku: dict[str, Any]) -> str | None:
    for obj in (sku, product):
        currency = _first_str(obj, "currency", "currency_code")
        if currency:
            return currency
        for key in ("price", "sale_price", "list_price", "retail_price"):
            raw = obj.get(key)
            if isinstance(raw, dict):
                currency = _first_str(raw, "currency", "currency_code")
                if currency:
                    return currency
    return None


def _extract_skus(product: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("skus", "sku_list", "product_skus", "seller_skus"):
        raw = product.get(key)
        if isinstance(raw, list):
            return [x for x in raw if isinstance(x, dict)]

    raw = _nested(product, "data", "skus")
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    return []


def _extract_products(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    candidates: Iterable[Any] = (
        payload.get("products"),
        payload.get("product_list"),
        payload.get("items"),
        data.get("products") if isinstance(data, dict) else None,
        data.get("product_list") if isinstance(data, dict) else None,
        data.get("items") if isinstance(data, dict) else None,
    )
    for raw in candidates:
        if isinstance(raw, list):
            return [x for x in raw if isinstance(x, dict)]
    return []


def _extract_product_detail(payload: dict[str, Any]) -> dict[str, Any] | None:
    data = payload.get("data")
    candidates: Iterable[Any] = (
        payload.get("product"),
        payload.get("item"),
        data.get("product") if isinstance(data, dict) else None,
        data.get("item") if isinstance(data, dict) else None,
        data if isinstance(data, dict) else None,
    )
    for raw in candidates:
        if isinstance(raw, dict):
            product_id = _first_str(raw, "id", "product_id", "global_product_id")
            if product_id or _extract_skus(raw):
                return raw
    return None


def _extract_next_page_token(payload: dict[str, Any]) -> str | None:
    data = payload.get("data")
    for obj in (payload, data if isinstance(data, dict) else {}):
        token = _first_str(obj, "next_page_token", "page_token", "next_token")
        if token:
            return token
    return None


async def sync_tts_products(
    *,
    db: AsyncSession,
    client: TTSClient,
    shop_id: str | None = None,
    page_size: int = 50,
    max_pages_per_shop: int = 20,
) -> ProductSyncResult:
    shops_stmt = select(TTSShop).order_by(TTSShop.shop_name, TTSShop.shop_id)
    if shop_id:
        shops_stmt = shops_stmt.where(TTSShop.shop_id == shop_id)
    shops = list((await db.execute(shops_stmt)).scalars())

    result = ProductSyncResult(shops_total=len(shops))
    for shop in shops:
        shop_result = ProductSyncShopResult(
            shop_id=shop.shop_id,
            shop_name=shop.shop_name,
        )
        result.shop_results.append(shop_result)
        try:
            await _sync_one_shop(
                db=db,
                client=client,
                shop=shop,
                page_size=page_size,
                max_pages=max_pages_per_shop,
                shop_result=shop_result,
            )
            result.shops_synced += 1
            result.products_seen += shop_result.products_seen
            result.skus_seen += shop_result.skus_seen
        except Exception as e:  # noqa: BLE001
            logger.exception("sync_tts_products failed shop_id=%s", shop.shop_id)
            shop_result.error = str(e)

    await refresh_tts_product_match_status(db)
    await db.commit()
    return result


async def _sync_one_shop(
    *,
    db: AsyncSession,
    client: TTSClient,
    shop: TTSShop,
    page_size: int,
    max_pages: int,
    shop_result: ProductSyncShopResult,
) -> None:
    page_token: str | None = None
    now = datetime.now(timezone.utc)

    for _ in range(max_pages):
        payload = await client.search_products(
            shop.shop_cipher,
            page_size=page_size,
            page_token=page_token,
        )
        shop_result.pages_seen += 1
        products = _extract_products(payload)
        for product in products:
            product_id = _first_str(product, "id", "product_id", "global_product_id")
            if not product_id:
                logger.warning("skip TT product without id shop_id=%s raw=%r", shop.shop_id, product)
                continue

            product_row = await _upsert_product(
                db,
                shop_id=shop.shop_id,
                product_id=product_id,
                product=product,
                synced_at=now,
            )
            shop_result.products_seen += 1

            skus = _extract_skus(product)
            if not skus:
                try:
                    detail_payload = await client.get_product_detail(
                        shop.shop_cipher,
                        product_id,
                    )
                    detail_product = _extract_product_detail(detail_payload)
                    if detail_product is not None:
                        product_row = await _upsert_product(
                            db,
                            shop_id=shop.shop_id,
                            product_id=product_id,
                            product={**product, **detail_product},
                            synced_at=now,
                        )
                        skus = _extract_skus(detail_product)
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "get TT product detail failed shop_id=%s product_id=%s error=%s",
                        shop.shop_id,
                        product_id,
                        e,
                    )

            for sku in skus:
                sku_id = _first_str(sku, "id", "sku_id", "seller_sku_id")
                if not sku_id:
                    logger.warning(
                        "skip TT sku without id shop_id=%s product_id=%s raw=%r",
                        shop.shop_id,
                        product_id,
                        sku,
                    )
                    continue
                await _upsert_sku(
                    db,
                    product_row=product_row,
                    product=product,
                    sku=sku,
                    synced_at=now,
                )
                shop_result.skus_seen += 1

        next_page_token = _extract_next_page_token(payload)
        if not next_page_token:
            break
        if next_page_token == page_token:
            logger.warning("TT product sync repeated page_token=%s", page_token)
            break
        page_token = next_page_token


async def _upsert_product(
    db: AsyncSession,
    *,
    shop_id: str,
    product_id: str,
    product: dict[str, Any],
    synced_at: datetime,
) -> TTSProduct:
    values = {
        "shop_id": shop_id,
        "tts_product_id": product_id,
        "product_name": _first_str(product, "title", "name", "product_name"),
        "status": _first_str(product, "status", "product_status", "listing_status"),
        "raw_payload": product,
        "synced_at": synced_at,
    }
    stmt = (
        pg_insert(TTSProduct)
        .values(**values)
        .on_conflict_do_update(
            constraint="uq_tts_products_shop_product",
            set_={
                "product_name": values["product_name"],
                "status": values["status"],
                "raw_payload": values["raw_payload"],
                "synced_at": values["synced_at"],
                "updated_at": func.now(),
            },
        )
        .returning(TTSProduct)
    )
    return (await db.execute(stmt)).scalar_one()


async def _upsert_sku(
    db: AsyncSession,
    *,
    product_row: TTSProduct,
    product: dict[str, Any],
    sku: dict[str, Any],
    synced_at: datetime,
) -> None:
    sku_id = _first_str(sku, "id", "sku_id", "seller_sku_id")
    if not sku_id:
        return

    values = {
        "product_id": product_row.id,
        "shop_id": product_row.shop_id,
        "tts_product_id": product_row.tts_product_id,
        "tts_sku_id": sku_id,
        "seller_sku": _first_str(sku, "seller_sku", "seller_sku_code", "identifier_code"),
        "sku_name": _first_str(sku, "sku_name", "name", "title", "sales_attribute"),
        "currency": _extract_currency(product, sku),
        "price_cents": _extract_price_cents(sku),
        "inventory_quantity": _to_int(
            sku.get("inventory_quantity")
            or sku.get("stock")
            or sku.get("available_stock")
            or sku.get("quantity")
        ),
        "status": _first_str(sku, "status", "sku_status", "listing_status"),
        "raw_payload": sku,
        "synced_at": synced_at,
    }
    stmt = (
        pg_insert(TTSProductSku)
        .values(**values)
        .on_conflict_do_update(
            constraint="uq_tts_product_skus_shop_sku",
            set_={
                "product_id": values["product_id"],
                "tts_product_id": values["tts_product_id"],
                "seller_sku": values["seller_sku"],
                "sku_name": values["sku_name"],
                "currency": values["currency"],
                "price_cents": values["price_cents"],
                "inventory_quantity": values["inventory_quantity"],
                "status": values["status"],
                "raw_payload": values["raw_payload"],
                "synced_at": values["synced_at"],
                "updated_at": func.now(),
            },
        )
    )
    await db.execute(stmt)


async def refresh_tts_product_match_status(db: AsyncSession) -> None:
    """按现有履约查找逻辑计算 TT SKU 快照的匹配状态。

    注意不能单独用 product_id 判断“已匹配”：一个 TT 商品下可能有多个 SKU/面值，
    product_id 只说明商品对了，不说明面值 SKU 对了。
    """
    rows = list((await db.execute(select(TTSProductSku))).scalars())
    for row in rows:
        exact_tts_sku = and_(
            SkuMapping.tts_product_id == row.tts_product_id,
            SkuMapping.tts_sku_id == row.tts_sku_id,
        )
        conditions = [exact_tts_sku]
        if row.seller_sku:
            conditions.append(SkuMapping.seller_sku == row.seller_sku)

        mappings = list(
            (
                await db.execute(
                    select(SkuMapping)
                    .where(or_(*conditions))
                    .limit(3)
                )
            ).scalars()
        )
        if not mappings:
            row.match_status = "unmatched"
            row.match_note = None
        elif len(mappings) == 1:
            m = mappings[0]
            if m.active:
                row.match_status = "matched"
                row.match_note = f"{m.internal_sku_id} / {m.face_value}"
            else:
                row.match_status = "conflict"
                row.match_note = f"已有停用映射：{m.internal_sku_id} / {m.face_value}"
        else:
            row.match_status = "conflict"
            row.match_note = ", ".join(
                f"{m.internal_sku_id}/{m.face_value}{'' if m.active else '(停用)'}"
                for m in mappings
            )
