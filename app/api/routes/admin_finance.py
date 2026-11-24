"""财务总览：GMV / 平台抽成 / 已售卡密成本 / 在库成本 / 毛利 / 应付。

数据口径（plan §九 财务模块）：
- GMV          = sum(tts_orders.total_amount) where status NOT IN (CANCELLED, RETURN)
- 平台抽成     = GMV * settings.tt_platform_commission_rate（估算）
- 已售卡密成本 = sum(unit_cost) where codes.status='used' AND purchase_order_id IS NOT NULL
- 在库成本     = sum(unit_cost) where codes.status IN ('free', 'locked') AND po_id IS NOT NULL
- 毛利         = GMV - 平台抽成 - 已售卡密成本
- 应付         = sum(total_cost - amount_paid) where payment_status != 'paid'
- 30 天月度趋势：按 purchase_date 月聚合 cost；按 update_time_tt 月聚合 GMV

为什么把 30s 缓存留到日后：先把准确数算对，缓存等性能瓶颈出现再加。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_settings_dep, verify_admin_session
from app.config import Settings
from app.models.code import Code
from app.models.order import TTSOrder
from app.models.purchase_order import PurchaseOrder


router = APIRouter(dependencies=[Depends(verify_admin_session)])


# ============== Schemas ==============


class FinanceOverview(BaseModel):
    range_days: int
    gmv: int
    gmv_currency: str
    platform_commission_estimated: int
    commission_rate: float
    sold_code_cost: int
    inventory_cost: int
    gross_profit: int
    payable: int
    payable_overdue: int
    legacy_codes_no_cost: int


class MonthlyPoint(BaseModel):
    month: str
    gmv: int
    cost: int


class FinanceMonthlyResponse(BaseModel):
    months: list[MonthlyPoint]


# ============== Routes ==============


@router.get("/overview", response_model=FinanceOverview)
async def overview(
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    range_days: int = Query(default=30, ge=1, le=365),
) -> FinanceOverview:
    since = datetime.now(tz=timezone.utc) - timedelta(days=range_days)
    since_ts = int(since.timestamp())

    # GMV：按 update_time_tt 在窗口内 + 状态非取消/退货
    gmv_row = (
        await db.execute(
            select(
                func.coalesce(func.sum(TTSOrder.total_amount), 0).label("gmv"),
                func.max(TTSOrder.currency).label("ccy"),
            ).where(
                and_(
                    TTSOrder.status.notin_(["CANCELLED", "RETURN"]),
                    TTSOrder.update_time_tt >= since_ts,
                )
            )
        )
    ).one()
    gmv = int(gmv_row.gmv or 0)
    ccy = gmv_row.ccy or "USD"

    commission = int(gmv * settings.tt_platform_commission_rate)

    # 已售卡密成本：codes.status=used + 关联 PO
    sold_cost = int(
        (
            await db.execute(
                select(func.coalesce(func.sum(PurchaseOrder.unit_cost), 0))
                .select_from(Code)
                .join(PurchaseOrder, PurchaseOrder.id == Code.purchase_order_id)
                .where(Code.status == "used", Code.used_at >= since)
            )
        ).scalar_one()
        or 0
    )

    # 在库成本：free + locked
    inv_cost = int(
        (
            await db.execute(
                select(func.coalesce(func.sum(PurchaseOrder.unit_cost), 0))
                .select_from(Code)
                .join(PurchaseOrder, PurchaseOrder.id == Code.purchase_order_id)
                .where(Code.status.in_(["free", "locked"]))
            )
        ).scalar_one()
        or 0
    )

    # 应付：未付清的 PO
    today = date.today()
    payable_row = (
        await db.execute(
            select(
                func.coalesce(
                    func.sum(PurchaseOrder.total_cost - PurchaseOrder.amount_paid), 0
                ).label("payable"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                and_(
                                    PurchaseOrder.payment_due_date.is_not(None),
                                    PurchaseOrder.payment_due_date < today,
                                ),
                                PurchaseOrder.total_cost - PurchaseOrder.amount_paid,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("overdue"),
            ).where(PurchaseOrder.payment_status != "paid")
        )
    ).one()

    legacy_no_cost = int(
        (
            await db.execute(
                select(func.count(Code.id)).where(
                    Code.purchase_order_id.is_(None),
                    Code.status.in_(["free", "locked", "used"]),
                )
            )
        ).scalar_one()
        or 0
    )

    return FinanceOverview(
        range_days=range_days,
        gmv=gmv,
        gmv_currency=ccy,
        platform_commission_estimated=commission,
        commission_rate=settings.tt_platform_commission_rate,
        sold_code_cost=sold_cost,
        inventory_cost=inv_cost,
        gross_profit=gmv - commission - sold_cost,
        payable=int(payable_row.payable or 0),
        payable_overdue=int(payable_row.overdue or 0),
        legacy_codes_no_cost=legacy_no_cost,
    )


@router.get("/monthly", response_model=FinanceMonthlyResponse)
async def monthly(
    db: AsyncSession = Depends(get_db),
    months_back: int = Query(default=6, ge=1, le=24),
) -> FinanceMonthlyResponse:
    """按月聚合 GMV 和采购成本；前端画双折线图。"""
    since = datetime.now(tz=timezone.utc) - timedelta(days=31 * months_back)
    since_ts = int(since.timestamp())

    # GMV by month
    gmv_rows = (
        await db.execute(
            select(
                func.to_char(
                    func.to_timestamp(TTSOrder.update_time_tt),
                    "YYYY-MM",
                ).label("month"),
                func.coalesce(func.sum(TTSOrder.total_amount), 0).label("gmv"),
            )
            .where(
                and_(
                    TTSOrder.status.notin_(["CANCELLED", "RETURN"]),
                    TTSOrder.update_time_tt >= since_ts,
                )
            )
            .group_by("month")
            .order_by("month")
        )
    ).all()

    cost_rows = (
        await db.execute(
            select(
                func.to_char(PurchaseOrder.purchase_date, "YYYY-MM").label("month"),
                func.coalesce(func.sum(PurchaseOrder.total_cost), 0).label("cost"),
            )
            .where(PurchaseOrder.purchase_date >= since.date())
            .group_by("month")
            .order_by("month")
        )
    ).all()

    by_month: dict[str, dict[str, int]] = {}
    for r in gmv_rows:
        by_month.setdefault(r.month, {"gmv": 0, "cost": 0})["gmv"] = int(r.gmv or 0)
    for r in cost_rows:
        by_month.setdefault(r.month, {"gmv": 0, "cost": 0})["cost"] = int(r.cost or 0)

    months = [
        MonthlyPoint(month=m, gmv=v["gmv"], cost=v["cost"])
        for m, v in sorted(by_month.items())
    ]
    return FinanceMonthlyResponse(months=months)
