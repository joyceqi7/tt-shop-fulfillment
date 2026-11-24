"""Dashboard：KPI + 近 14 日 GMV 折线 + 低库存告警 + 最近订单。

聚合逻辑独立于 finance 路由，是因为 dashboard 关心"运营节奏"，
finance 关心"账面盈亏"。两边数据口径未来可能分叉。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_settings_dep, verify_admin_session
from app.config import Settings
from app.models.code import Code
from app.models.order import TTSOrder, TTSOrderLine
from app.models.purchase_order import PurchaseOrder
from app.models.supplier import Supplier


router = APIRouter(dependencies=[Depends(verify_admin_session)])


# ============== Schemas ==============


class KPIs(BaseModel):
    gmv_today: int
    orders_today: int
    pending_lines: int
    free_codes_total: int
    used_today: int
    failed_today: int
    pending_user_payments: int


class TrendPoint(BaseModel):
    date: str
    gmv: int
    orders: int


class LowStockRow(BaseModel):
    sku_id: str
    face_value: int
    free: int
    threshold: int


class RecentOrder(BaseModel):
    order_id: str
    shop_id: str
    status: str
    total_amount: int | None
    currency: str | None
    update_time_tt: int | None
    failed_lines: int


class SupplierSalesRow(BaseModel):
    supplier_id: int
    supplier_name: str
    sold_codes: int
    gmv_estimated: int
    cost: int
    gross_profit_estimated: int


class DashboardResponse(BaseModel):
    kpis: KPIs
    trend_14d: list[TrendPoint]
    low_stock: list[LowStockRow]
    recent_failed_orders: list[RecentOrder]
    supplier_sales_30d: list[SupplierSalesRow]


@router.get("/overview", response_model=DashboardResponse)
async def dashboard_overview(
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> DashboardResponse:
    now = datetime.now(tz=timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_start_ts = int(today_start.timestamp())

    # KPI: today GMV / order count
    today_row = (
        await db.execute(
            select(
                func.coalesce(func.sum(TTSOrder.total_amount), 0).label("gmv"),
                func.count(TTSOrder.order_id).label("orders"),
            ).where(
                and_(
                    TTSOrder.status.notin_(["CANCELLED", "RETURN"]),
                    TTSOrder.update_time_tt >= today_start_ts,
                )
            )
        )
    ).one()

    pending_lines = int(
        (
            await db.execute(
                select(func.count(TTSOrderLine.order_line_id)).where(
                    TTSOrderLine.status == "pending"
                )
            )
        ).scalar_one()
    )

    free_total = int(
        (
            await db.execute(
                select(func.count(Code.id)).where(Code.status == "free")
            )
        ).scalar_one()
    )

    used_today = int(
        (
            await db.execute(
                select(func.count(Code.id)).where(
                    Code.status == "used", Code.used_at >= today_start
                )
            )
        ).scalar_one()
    )

    failed_today = int(
        (
            await db.execute(
                select(func.count(TTSOrderLine.order_line_id))
                .where(
                    TTSOrderLine.status == "failed",
                    TTSOrderLine.updated_at >= today_start,
                )
            )
        ).scalar_one()
    )
    pending_user_payments = int(
        (
            await db.execute(
                select(func.count(TTSOrder.order_id)).where(
                    TTSOrder.status.in_(
                        ["UNPAID", "AWAITING_PAYMENT", "PENDING_PAYMENT"]
                    )
                )
            )
        ).scalar_one()
    )

    # 14 天趋势
    fourteen_ago = today_start - timedelta(days=13)
    fourteen_ago_ts = int(fourteen_ago.timestamp())
    trend_rows = (
        await db.execute(
            select(
                func.to_char(
                    func.to_timestamp(TTSOrder.update_time_tt), "YYYY-MM-DD"
                ).label("d"),
                func.coalesce(func.sum(TTSOrder.total_amount), 0).label("gmv"),
                func.count(TTSOrder.order_id).label("orders"),
            )
            .where(
                and_(
                    TTSOrder.status.notin_(["CANCELLED", "RETURN"]),
                    TTSOrder.update_time_tt >= fourteen_ago_ts,
                )
            )
            .group_by("d")
            .order_by("d")
        )
    ).all()

    # 低库存：active SKU 的 (sku_id, face_value) 聚合 < threshold
    low_stock_rows = (
        await db.execute(
            select(
                Code.sku_id,
                Code.face_value,
                func.count(Code.id).label("free"),
            )
            .where(Code.status == "free")
            .group_by(Code.sku_id, Code.face_value)
            .having(func.count(Code.id) < settings.stock_alert_threshold)
            .order_by(func.count(Code.id))
            .limit(10)
        )
    ).all()

    # 最近 5 个有 failed 行的订单
    failed_lines_subq = (
        select(
            TTSOrderLine.order_id,
            func.sum(case((TTSOrderLine.status == "failed", 1), else_=0)).label(
                "failed_lines"
            ),
        )
        .group_by(TTSOrderLine.order_id)
        .subquery()
    )
    recent_rows = (
        await db.execute(
            select(TTSOrder, failed_lines_subq.c.failed_lines)
            .join(
                failed_lines_subq,
                failed_lines_subq.c.order_id == TTSOrder.order_id,
            )
            .where(failed_lines_subq.c.failed_lines > 0)
            .order_by(TTSOrder.update_time_tt.desc().nullslast())
            .limit(5)
        )
    ).all()

    # 近 30 天供货商销售（基于 used code 估算）
    supplier_rows = (
        await db.execute(
            select(
                Supplier.id.label("supplier_id"),
                Supplier.name.label("supplier_name"),
                func.count(Code.id).label("sold_codes"),
                func.coalesce(func.sum(Code.face_value), 0).label("gmv_est"),
                func.coalesce(func.sum(PurchaseOrder.unit_cost), 0).label("cost"),
            )
            .select_from(Code)
            .join(PurchaseOrder, PurchaseOrder.id == Code.purchase_order_id)
            .join(Supplier, Supplier.id == PurchaseOrder.supplier_id)
            .where(Code.status == "used", Code.used_at >= now - timedelta(days=30))
            .group_by(Supplier.id, Supplier.name)
            .order_by(func.coalesce(func.sum(Code.face_value), 0).desc())
            .limit(8)
        )
    ).all()

    return DashboardResponse(
        kpis=KPIs(
            gmv_today=int(today_row.gmv or 0),
            orders_today=int(today_row.orders or 0),
            pending_lines=pending_lines,
            free_codes_total=free_total,
            used_today=used_today,
            failed_today=failed_today,
            pending_user_payments=pending_user_payments,
        ),
        trend_14d=[
            TrendPoint(
                date=r.d, gmv=int(r.gmv or 0), orders=int(r.orders or 0)
            )
            for r in trend_rows
        ],
        low_stock=[
            LowStockRow(
                sku_id=r.sku_id,
                face_value=r.face_value,
                free=int(r.free or 0),
                threshold=settings.stock_alert_threshold,
            )
            for r in low_stock_rows
        ],
        recent_failed_orders=[
            RecentOrder(
                order_id=r.TTSOrder.order_id,
                shop_id=r.TTSOrder.shop_id,
                status=r.TTSOrder.status,
                total_amount=r.TTSOrder.total_amount,
                currency=r.TTSOrder.currency,
                update_time_tt=r.TTSOrder.update_time_tt,
                failed_lines=int(r.failed_lines or 0),
            )
            for r in recent_rows
        ],
        supplier_sales_30d=[
            SupplierSalesRow(
                supplier_id=int(r.supplier_id),
                supplier_name=str(r.supplier_name),
                sold_codes=int(r.sold_codes or 0),
                gmv_estimated=int(r.gmv_est or 0),
                cost=int(r.cost or 0),
                gross_profit_estimated=int(
                    int(r.gmv_est or 0)
                    - int(r.cost or 0)
                    - int(int(r.gmv_est or 0) * settings.tt_platform_commission_rate)
                ),
            )
            for r in supplier_rows
        ],
    )
