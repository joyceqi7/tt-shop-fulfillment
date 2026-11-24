"""FastAPI 应用入口。

启动方式：
    uvicorn app.api.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from arq import create_pool
from fastapi import FastAPI

from app.api.routes import (
    admin,
    admin_audit,
    admin_auth,
    admin_dashboard,
    admin_finance,
    admin_inventory,
    admin_orders,
    admin_procurement,
    admin_purchases,
    admin_skus,
    admin_supplier_orders,
    admin_supplier_sku_links,
    admin_suppliers,
    admin_tts_products,
    health,
    metrics,
    oauth,
    webhooks,
    wgcards,
)
from app.config import get_settings
from app.observability import init_sentry
from app.workers.redis_settings import build_redis_settings


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info("starting app env=%s", settings.app_env)

    init_sentry()

    app.state.arq_pool = await create_pool(build_redis_settings())
    logger.info("arq pool ready")

    try:
        yield
    finally:
        await app.state.arq_pool.aclose()
        logger.info("arq pool closed")


app = FastAPI(
    title="TT Shop Fulfillment",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
)

app.include_router(health.router, prefix="/health", tags=["health"])
app.include_router(webhooks.router, prefix="/webhooks", tags=["webhook"])
app.include_router(admin.router, prefix="/admin", tags=["admin"])
app.include_router(admin_auth.router, prefix="/admin/auth", tags=["admin-auth"])
app.include_router(admin_orders.router, prefix="/admin/orders", tags=["admin-orders"])
app.include_router(
    admin_inventory.router, prefix="/admin/inventory", tags=["admin-inventory"]
)
app.include_router(admin_skus.router, prefix="/admin/skus", tags=["admin-skus"])
app.include_router(
    admin_tts_products.router,
    prefix="/admin/tts-products",
    tags=["admin-tts-products"],
)
app.include_router(
    admin_suppliers.router, prefix="/admin/suppliers", tags=["admin-suppliers"]
)
app.include_router(
    admin_supplier_orders.router,
    prefix="/admin/supplier-orders",
    tags=["admin-supplier-orders"],
)
app.include_router(
    admin_supplier_sku_links.router,
    prefix="/admin/supplier-sku-links",
    tags=["admin-supplier-sku-links"],
)
app.include_router(
    admin_procurement.router,
    prefix="/admin/procurement",
    tags=["admin-procurement"],
)
app.include_router(
    admin_purchases.router, prefix="/admin/purchases", tags=["admin-purchases"]
)
app.include_router(
    admin_finance.router, prefix="/admin/finance", tags=["admin-finance"]
)
app.include_router(
    admin_dashboard.router, prefix="/admin/dashboard", tags=["admin-dashboard"]
)
app.include_router(admin_audit.router, prefix="/admin/audit", tags=["admin-audit"])
app.include_router(metrics.router, prefix="/metrics", tags=["metrics"])
app.include_router(oauth.router, prefix="/oauth", tags=["oauth"])
app.include_router(wgcards.router, prefix="/admin", tags=["wgcards"])


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {"service": "tt-shop-fulfillment", "docs": "/docs", "health": "/health/ready"}
