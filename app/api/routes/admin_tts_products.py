"""TT 商品中心：只读同步 TT 已上架商品，再匹配内部 SKU。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_redis, get_settings_dep, verify_admin_session
from app.auth.token_manager import TokenManager
from app.config import Settings
from app.db.session import session_factory
from app.models.shop import TTSShop
from app.models.tts_product import TTSProduct, TTSProductSku
from app.services.admin_audit import write_audit
from app.services.product_sync import sync_tts_products
from app.services.tts_client import TTSClient


router = APIRouter(dependencies=[Depends(verify_admin_session)])


class TTSProductSkuRow(BaseModel):
    id: int
    shop_id: str
    shop_name: str | None
    tts_product_id: str
    tts_sku_id: str
    seller_sku: str | None
    product_name: str | None
    sku_name: str | None
    status: str | None
    product_status: str | None
    currency: str | None
    price_cents: int | None
    inventory_quantity: int | None
    match_status: str | None
    match_note: str | None
    synced_at: str
    updated_at: str


class TTSProductListResponse(BaseModel):
    items: list[TTSProductSkuRow]
    total: int
    limit: int
    offset: int


class TTSProductSyncRequest(BaseModel):
    shop_id: str | None = Field(default=None, max_length=64)
    page_size: int = Field(default=50, ge=1, le=100)
    max_pages_per_shop: int = Field(default=20, ge=1, le=100)


class TTSProductSyncShopResult(BaseModel):
    shop_id: str
    shop_name: str | None
    products_seen: int
    skus_seen: int
    pages_seen: int
    error: str | None


class TTSProductSyncResponse(BaseModel):
    shops_total: int
    shops_synced: int
    products_seen: int
    skus_seen: int
    shop_results: list[TTSProductSyncShopResult]


class TTSProductStatsResponse(BaseModel):
    total_skus: int
    matched_skus: int
    unmatched_skus: int
    conflict_skus: int
    last_synced_at: str | None


@router.get(
    "",
    response_model=TTSProductListResponse,
)
async def list_tts_products(
    db: AsyncSession = Depends(get_db),
    match_status: str | None = Query(default=None),
    shop_id: str | None = Query(default=None),
    q: str | None = Query(default=None, description="商品名 / seller_sku / TT ID"),
    limit: int = Query(default=100, ge=1, le=300),
    offset: int = Query(default=0, ge=0),
) -> TTSProductListResponse:
    conds = []
    if match_status:
        conds.append(TTSProductSku.match_status == match_status)
    if shop_id:
        conds.append(TTSProductSku.shop_id == shop_id)
    if q:
        like = f"%{q.strip()}%"
        conds.append(
            TTSProduct.product_name.ilike(like)
            | TTSProductSku.sku_name.ilike(like)
            | TTSProductSku.seller_sku.ilike(like)
            | TTSProductSku.tts_product_id.ilike(like)
            | TTSProductSku.tts_sku_id.ilike(like)
        )

    base = (
        select(TTSProductSku)
        .join(TTSProduct, TTSProduct.id == TTSProductSku.product_id)
        .outerjoin(TTSShop, TTSShop.shop_id == TTSProductSku.shop_id)
    )
    if conds:
        base = base.where(and_(*conds))

    total = int(
        (
            await db.execute(
                select(func.count()).select_from(base.subquery())
            )
        ).scalar_one()
    )

    stmt = (
        select(TTSProductSku, TTSProduct, TTSShop.shop_name)
        .join(TTSProduct, TTSProduct.id == TTSProductSku.product_id)
        .outerjoin(TTSShop, TTSShop.shop_id == TTSProductSku.shop_id)
        .order_by(
            TTSProductSku.match_status.desc().nullslast(),
            TTSProduct.product_name,
            TTSProductSku.sku_name,
            TTSProductSku.id.desc(),
        )
        .limit(limit)
        .offset(offset)
    )
    if conds:
        stmt = stmt.where(and_(*conds))

    rows = (await db.execute(stmt)).all()
    return TTSProductListResponse(
        items=[
            TTSProductSkuRow(
                id=sku.id,
                shop_id=sku.shop_id,
                shop_name=shop_name,
                tts_product_id=sku.tts_product_id,
                tts_sku_id=sku.tts_sku_id,
                seller_sku=sku.seller_sku,
                product_name=product.product_name,
                sku_name=sku.sku_name,
                status=sku.status,
                product_status=product.status,
                currency=sku.currency,
                price_cents=sku.price_cents,
                inventory_quantity=sku.inventory_quantity,
                match_status=sku.match_status,
                match_note=sku.match_note,
                synced_at=sku.synced_at.isoformat(),
                updated_at=sku.updated_at.isoformat(),
            )
            for sku, product, shop_name in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/stats",
    response_model=TTSProductStatsResponse,
)
async def tts_product_stats(
    db: AsyncSession = Depends(get_db),
) -> TTSProductStatsResponse:
    row = (
        await db.execute(
            select(
                func.count(TTSProductSku.id).label("total"),
                func.count(TTSProductSku.id)
                .filter(TTSProductSku.match_status == "matched")
                .label("matched"),
                func.count(TTSProductSku.id)
                .filter(TTSProductSku.match_status == "unmatched")
                .label("unmatched"),
                func.count(TTSProductSku.id)
                .filter(TTSProductSku.match_status == "conflict")
                .label("conflict"),
                func.max(TTSProductSku.synced_at).label("last_synced_at"),
            )
        )
    ).one()
    return TTSProductStatsResponse(
        total_skus=int(row.total or 0),
        matched_skus=int(row.matched or 0),
        unmatched_skus=int(row.unmatched or 0),
        conflict_skus=int(row.conflict or 0),
        last_synced_at=row.last_synced_at.isoformat() if row.last_synced_at else None,
    )


@router.post(
    "/sync",
    response_model=TTSProductSyncResponse,
)
async def sync_products(
    request: Request,
    body: TTSProductSyncRequest = TTSProductSyncRequest(),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    settings: Settings = Depends(get_settings_dep),
    operator: str = Depends(verify_admin_session),
) -> TTSProductSyncResponse:
    tm = TokenManager(
        settings=settings,
        session_factory=session_factory(),
        redis=redis,
    )
    client = TTSClient(settings=settings, token_manager=tm)
    result = await sync_tts_products(
        db=db,
        client=client,
        shop_id=body.shop_id,
        page_size=body.page_size,
        max_pages_per_shop=body.max_pages_per_shop,
    )
    await write_audit(
        db,
        operator=operator,
        action="tts_product_sync",
        request=request,
        target_type="tts_products",
        target_id=body.shop_id,
        extra={
            "shop_id": body.shop_id,
            "page_size": body.page_size,
            "max_pages_per_shop": body.max_pages_per_shop,
            "shops_synced": result.shops_synced,
            "products_seen": result.products_seen,
            "skus_seen": result.skus_seen,
            "errors": [
                {"shop_id": r.shop_id, "error": r.error}
                for r in result.shop_results
                if r.error
            ],
        },
        commit=False,
    )
    await db.commit()
    return TTSProductSyncResponse(
        shops_total=result.shops_total,
        shops_synced=result.shops_synced,
        products_seen=result.products_seen,
        skus_seen=result.skus_seen,
        shop_results=[
            TTSProductSyncShopResult(
                shop_id=r.shop_id,
                shop_name=r.shop_name,
                products_seen=r.products_seen,
                skus_seen=r.skus_seen,
                pages_seen=r.pages_seen,
                error=r.error,
            )
            for r in result.shop_results
        ],
    )
