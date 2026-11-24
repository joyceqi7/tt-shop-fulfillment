"""订单：列表 / 详情 / 重发码（cookie session 版本）。

与历史 `/admin/reship`（X-Admin-Token 兜底）共存：
- 历史接口保留兼容运维脚本和应急 `curl`
- 后台前端走 cookie 版，所有动作落 admin_audit_log
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Literal

import httpx
from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import Text, and_, case, cast, exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_arq_pool,
    get_db,
    get_redis,
    get_settings_dep,
    require_permission,
    verify_admin_session,
)
from app.auth.token_manager import TokenManager
from app.config import Settings
from app.db.session import session_factory
from app.models.fulfillment_log import FulfillmentLog
from app.models.order import TTSOrder, TTSOrderLine
from app.models.shop import TTSShop
from app.models.webhook_event import WebhookEvent
from app.services.admin_audit import write_audit
from app.services import code_inventory
from app.services.fulfillment import find_mapping_for_line
from app.services.order_lock import order_lock
from app.services.orders import upsert_order_from_tt
from app.services.tts_client import TTSApiError, TTSClient


logger = logging.getLogger(__name__)


router = APIRouter(dependencies=[Depends(verify_admin_session)])


# ============== Schemas ==============


class OrderListItem(BaseModel):
    order_id: str
    shop_id: str
    shop_name: str | None
    buyer_email: str | None
    buyer_tt_id: str | None
    product_name: str | None
    tt_settlement_status: str | None
    status: str
    line_count: int
    total_amount: int | None
    currency: str | None
    create_time_tt: int | None
    update_time_tt: int | None
    pending_lines: int
    shipped_lines: int
    failed_lines: int


class OrderListResponse(BaseModel):
    items: list[OrderListItem]
    total: int
    limit: int
    offset: int


class OrderLineDetail(BaseModel):
    order_line_id: str
    seller_sku: str | None
    tts_product_id: str | None
    tts_sku_id: str | None
    quantity: int
    unit_price: int | None
    currency: str | None
    status: str
    redeemed_code_id: int | None
    redeemed_at: str | None
    failed_reason: str | None


class FulfillmentLogItem(BaseModel):
    id: int
    api_endpoint: str
    api_method: str
    http_status: int | None
    api_code: int | None
    api_message: str | None
    request_id: str | None
    latency_ms: int | None
    error: str | None
    created_at: str
    order_line_id: str | None


class OrderDetailResponse(BaseModel):
    order_id: str
    shop_id: str
    shop_name: str | None
    status: str
    total_amount: int | None
    currency: str | None
    create_time_tt: int | None
    update_time_tt: int | None
    sla_deadline: str | None
    lines: list[OrderLineDetail]
    fulfillment_logs: list[FulfillmentLogItem]


class ReshipRequest(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class ReshipResponse(BaseModel):
    job_id: str
    event_id: int
    order_id: str
    status: Literal["queued"] = "queued"


class PullOrderRequest(BaseModel):
    order_id: str = Field(min_length=1, max_length=64)
    note: str | None = Field(default=None, max_length=500)
    enqueue_fulfillment: bool = True


class PullOrderResponse(BaseModel):
    order_id: str
    shop_id: str
    order_status: str
    line_count: int
    event_id: int | None = None
    job_id: str | None = None
    action: Literal["upserted"] = "upserted"


class SyncOrdersResponse(BaseModel):
    status: Literal["queued"] = "queued"
    job_id: str


class ManualCodeReshipRequest(BaseModel):
    plaintext_code: str = Field(min_length=1, max_length=4000)
    note: str | None = Field(default=None, max_length=500)


class ManualCodeReshipResponse(BaseModel):
    order_id: str
    order_line_id: str
    code_id: int
    status: Literal["shipped"] = "shipped"
    api_code: int | None = None
    api_message: str | None = None
    request_id: str | None = None


def _pick_payload_value(payload: dict, keys: list[str]) -> str | None:
    for key in keys:
        v = payload.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)):
            return str(v)
    return None


def _extract_order_meta(order: TTSOrder) -> tuple[str | None, str | None, str | None, str | None]:
    payload = order.raw_payload if isinstance(order.raw_payload, dict) else {}
    buyer = payload.get("buyer") if isinstance(payload.get("buyer"), dict) else {}
    user_info = payload.get("user_info") if isinstance(payload.get("user_info"), dict) else {}
    customer = payload.get("customer") if isinstance(payload.get("customer"), dict) else {}
    payment = payload.get("payment") if isinstance(payload.get("payment"), dict) else {}

    buyer_email = (
        _pick_payload_value(buyer, ["email", "buyer_email"])
        or _pick_payload_value(user_info, ["email", "buyer_email"])
        or _pick_payload_value(customer, ["email", "buyer_email"])
        or _pick_payload_value(payload, ["buyer_email", "customer_email"])
    )
    buyer_tt_id = (
        _pick_payload_value(buyer, ["buyer_id", "user_id", "tt_user_id"])
        or _pick_payload_value(user_info, ["user_id", "buyer_id", "tt_user_id"])
        or _pick_payload_value(customer, ["id", "user_id"])
        or _pick_payload_value(payload, ["buyer_id", "tt_user_id", "user_id"])
    )
    settlement = _pick_payload_value(
        payment, ["settlement_status", "payment_status"]
    ) or _pick_payload_value(payload, ["settlement_status", "payment_status"])
    return buyer_email, buyer_tt_id, None, settlement


async def _send_manual_code_reship(
    *,
    request: Request,
    db: AsyncSession,
    settings: Settings,
    redis,
    operator: str,
    order_id: str,
    order_line_id: str,
    shop_id: str,
    shop_cipher: str,
    code_id: int,
    plaintext: str,
    note: str | None,
) -> ManualCodeReshipResponse:
    order_info_list = [
        {
            "order_line_id": order_line_id,
            "redeem_info": {"redeem_type": "CODE", "redeem_data": plaintext},
            "source_unique_id": str(code_id),
        }
    ]
    log_req = {
        "manual_code": True,
        "order_line_ids": [order_line_id],
        "source_unique_ids": [str(code_id)],
        "order_info_list_count": 1,
    }
    endpoint = "/fulfillment/202601/redeem_info/callback"

    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(
            proxy=settings.tts_http_proxy or None, trust_env=False, timeout=30
        ) as http:
            tm = TokenManager(settings, session_factory(), redis, http_client=http)
            client = TTSClient(settings=settings, token_manager=tm, http_client=http)
            resp = await client.redeem_info_callback(
                shop_cipher,
                order_id,
                order_info_list,
            )
    except TTSApiError as e:
        latency_ms = int((time.monotonic() - t0) * 1000)
        await code_inventory.void(db, code_id)
        line = await db.get(TTSOrderLine, order_line_id)
        if line is not None:
            line.redeemed_code_id = None
            line.status = "failed"
            line.failed_reason = "manual_code_redeem_failed"
        db.add(
            FulfillmentLog(
                order_id=order_id,
                order_line_id=order_line_id,
                shop_id=shop_id,
                api_endpoint=endpoint,
                api_method="POST",
                request_body=log_req,
                response_body=e.response_body if isinstance(e.response_body, dict) else None,
                http_status=e.http_status,
                api_code=e.code,
                api_message=str(e),
                request_id=e.request_id,
                latency_ms=latency_ms,
                error=str(e),
            )
        )
        await db.commit()
        raise HTTPException(status_code=502, detail=f"TT 发货回调失败：{e}") from e
    except Exception as e:
        latency_ms = int((time.monotonic() - t0) * 1000)
        await code_inventory.void(db, code_id)
        line = await db.get(TTSOrderLine, order_line_id)
        if line is not None:
            line.redeemed_code_id = None
            line.status = "failed"
            line.failed_reason = "manual_code_redeem_failed"
        db.add(
            FulfillmentLog(
                order_id=order_id,
                order_line_id=order_line_id,
                shop_id=shop_id,
                api_endpoint=endpoint,
                api_method="POST",
                request_body=log_req,
                latency_ms=latency_ms,
                error=str(e),
            )
        )
        await db.commit()
        raise HTTPException(status_code=502, detail=f"TT 发货回调失败：{e}") from e

    latency_ms = int((time.monotonic() - t0) * 1000)
    now = datetime.now(tz=timezone.utc)
    await code_inventory.mark_used(db, code_id)
    line = await db.get(TTSOrderLine, order_line_id)
    if line is not None:
        line.status = "shipped"
        line.redeemed_at = now
        line.failed_reason = None
        line.redeemed_code_id = code_id
    db.add(
        FulfillmentLog(
            order_id=order_id,
            order_line_id=order_line_id,
            shop_id=shop_id,
            api_endpoint=endpoint,
            api_method="POST",
            request_body=log_req,
            response_body=resp,
            http_status=200,
            api_code=resp.get("code"),
            api_message=resp.get("message"),
            request_id=resp.get("request_id"),
            latency_ms=latency_ms,
        )
    )
    await db.commit()

    await write_audit(
        db,
        operator=operator,
        action="manual_code_reship",
        request=request,
        target_type="order_line",
        target_id=order_line_id,
        extra={
            "order_id": order_id,
            "code_id": code_id,
            "note": note,
            "source_unique_id": str(code_id),
        },
    )

    logger.info(
        "manual code reship op=%s order=%s line=%s code_id=%s",
        operator,
        order_id,
        order_line_id,
        code_id,
    )

    return ManualCodeReshipResponse(
        order_id=order_id,
        order_line_id=order_line_id,
        code_id=code_id,
        api_code=resp.get("code"),
        api_message=resp.get("message"),
        request_id=resp.get("request_id"),
    )


@router.post(
    "/sync",
    response_model=SyncOrdersResponse,
    summary="手动同步 TT 订单（触发一次对账拉单）",
    dependencies=[Depends(require_permission("orders:reship"))],
)
async def sync_orders(
    request: Request,
    db: AsyncSession = Depends(get_db),
    arq: ArqRedis = Depends(get_arq_pool),
    operator: str = Depends(verify_admin_session),
) -> SyncOrdersResponse:
    job = await arq.enqueue_job("manual_reconciliation_task")
    job_id = job.job_id if job is not None else "unknown"
    await write_audit(
        db,
        operator=operator,
        action="order_sync_queued",
        request=request,
        target_type="orders",
        target_id=None,
        extra={"job_id": job_id},
    )
    return SyncOrdersResponse(job_id=job_id)


# ============== Routes ==============


@router.post(
    "/pull",
    response_model=PullOrderResponse,
    summary="按 TT 订单号手动补拉订单并可触发履约",
    dependencies=[Depends(require_permission("orders:reship"))],
)
async def pull_order(
    request: Request,
    body: PullOrderRequest,
    db: AsyncSession = Depends(get_db),
    arq: ArqRedis = Depends(get_arq_pool),
    settings: Settings = Depends(get_settings_dep),
    redis=Depends(get_redis),
    operator: str = Depends(verify_admin_session),
) -> PullOrderResponse:
    order_id = body.order_id.strip()
    if not order_id:
        raise HTTPException(status_code=422, detail="订单号不能为空")

    shops = list((await db.execute(select(TTSShop))).scalars())
    if not shops:
        raise HTTPException(status_code=409, detail="没有已授权店铺，请先完成 TT 授权")

    found_order: dict | None = None
    found_shop: TTSShop | None = None
    errors: list[str] = []

    async with httpx.AsyncClient(
        proxy=settings.tts_http_proxy or None,
        trust_env=False,
        timeout=30,
    ) as http:
        tm = TokenManager(settings, session_factory(), redis, http_client=http)
        client = TTSClient(settings=settings, token_manager=tm, http_client=http)
        for shop in shops:
            try:
                resp = await client.get_order_detail(shop.shop_cipher, [order_id])
                orders = (resp.get("data") or {}).get("orders") or []
            except TTSApiError as e:
                errors.append(
                    f"{shop.shop_id}: code={e.code} request_id={e.request_id} {e}"
                )
                continue
            except Exception as e:  # noqa: BLE001
                errors.append(f"{shop.shop_id}: {type(e).__name__}: {e}")
                continue
            if orders:
                found_order = orders[0]
                found_shop = shop
                break

    if found_order is None or found_shop is None:
        detail = "TT 未返回该订单；请确认订单号、授权店铺和订单归属"
        if errors:
            detail = f"{detail}。最近错误：{' | '.join(errors[:3])}"
        raise HTTPException(status_code=404, detail=detail)

    async with order_lock(redis, order_id):
        tts_order, lines = await upsert_order_from_tt(
            db,
            order=found_order,
            shop_id=found_shop.shop_id,
        )

        event_id: int | None = None
        job_id: str | None = None
        if body.enqueue_fulfillment:
            event = WebhookEvent(
                dedup_key=f"admin-pull:{order_id}:{uuid.uuid4().hex[:12]}",
                event_type="ADMIN_PULL",
                shop_id=found_shop.shop_id,
                order_id=order_id,
                raw_payload={
                    "source": "admin_pull_order",
                    "operator": operator,
                    "order_id": order_id,
                    "shop_id": found_shop.shop_id,
                    "note": body.note,
                },
            )
            db.add(event)
            await db.flush()
            event_id = int(event.id)

        await write_audit(
            db,
            operator=operator,
            action="order_pull",
            request=request,
            target_type="order",
            target_id=order_id,
            extra={
                "shop_id": found_shop.shop_id,
                "order_status": tts_order.status,
                "line_count": len(lines),
                "event_id": event_id,
                "note": body.note,
                "errors": errors[:3],
            },
            commit=False,
        )
        await db.commit()

    if event_id is not None:
        job = await arq.enqueue_job("fulfill_order", event_id)
        job_id = job.job_id if job is not None else "unknown"

    logger.info(
        "admin pull order op=%s order=%s shop=%s status=%s event=%s job=%s",
        operator,
        order_id,
        found_shop.shop_id,
        tts_order.status,
        event_id,
        job_id,
    )

    return PullOrderResponse(
        order_id=order_id,
        shop_id=found_shop.shop_id,
        order_status=tts_order.status,
        line_count=len(lines),
        event_id=event_id,
        job_id=job_id,
    )


@router.get(
    "",
    response_model=OrderListResponse,
    summary="订单列表（支持状态 + 顾客 + 商品关键词过滤）",
    dependencies=[Depends(require_permission("orders:read"))],
)
async def list_orders(
    db: AsyncSession = Depends(get_db),
    status: str | None = Query(default=None, description="精确 status 过滤"),
    q: str | None = Query(default=None, description="按 order_id 模糊搜（前缀匹配最快）"),
    buyer_email: str | None = Query(default=None, description="顾客邮箱（模糊）"),
    buyer_tt_id: str | None = Query(default=None, description="顾客 TT 用户 ID（模糊）"),
    product_name: str | None = Query(default=None, description="产品名/SKU（模糊）"),
    tt_settlement_status: str | None = Query(default=None, description="TT 结款状态（模糊）"),
    only_failed: bool = Query(default=False, description="只看有 failed line 的订单"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> OrderListResponse:
    # 子查询：统计每个 order 的 line 状态分布
    line_stats = (
        select(
            TTSOrderLine.order_id,
            func.count(TTSOrderLine.order_line_id).label("line_count"),
            func.coalesce(
                func.sum(case((TTSOrderLine.status == "pending", 1), else_=0)), 0
            ).label("pending_lines"),
            func.coalesce(
                func.sum(case((TTSOrderLine.status == "shipped", 1), else_=0)), 0
            ).label("shipped_lines"),
            func.coalesce(
                func.sum(case((TTSOrderLine.status == "failed", 1), else_=0)), 0
            ).label("failed_lines"),
            func.max(
                func.coalesce(
                    TTSOrderLine.seller_sku,
                    TTSOrderLine.tts_product_id,
                    TTSOrderLine.tts_sku_id,
                )
            ).label("product_name_hint"),
        )
        .group_by(TTSOrderLine.order_id)
        .subquery()
    )

    base = (
        select(
            TTSOrder,
            TTSShop.shop_name,
            func.coalesce(line_stats.c.line_count, 0).label("line_count"),
            func.coalesce(line_stats.c.pending_lines, 0).label("pending_lines"),
            func.coalesce(line_stats.c.shipped_lines, 0).label("shipped_lines"),
            func.coalesce(line_stats.c.failed_lines, 0).label("failed_lines"),
            line_stats.c.product_name_hint,
        )
        .outerjoin(TTSShop, TTSShop.shop_id == TTSOrder.shop_id)
        .outerjoin(line_stats, line_stats.c.order_id == TTSOrder.order_id)
    )
    conds = []
    if status:
        conds.append(TTSOrder.status == status)
    if q:
        conds.append(TTSOrder.order_id.ilike(f"{q}%"))
    if buyer_email:
        conds.append(cast(TTSOrder.raw_payload, Text).ilike(f"%{buyer_email.strip()}%"))
    if buyer_tt_id:
        conds.append(cast(TTSOrder.raw_payload, Text).ilike(f"%{buyer_tt_id.strip()}%"))
    if tt_settlement_status:
        conds.append(
            cast(TTSOrder.raw_payload, Text).ilike(f"%{tt_settlement_status.strip()}%")
        )
    if product_name:
        kw = f"%{product_name.strip()}%"
        conds.append(
            exists(
                select(TTSOrderLine.order_line_id).where(
                    and_(
                        TTSOrderLine.order_id == TTSOrder.order_id,
                        or_(
                            TTSOrderLine.seller_sku.ilike(kw),
                            TTSOrderLine.tts_product_id.ilike(kw),
                            TTSOrderLine.tts_sku_id.ilike(kw),
                        ),
                    )
                )
            )
        )
    if only_failed:
        conds.append(line_stats.c.failed_lines > 0)
    if conds:
        base = base.where(and_(*conds))

    # total
    count_q = select(func.count()).select_from(
        select(TTSOrder.order_id).where(*conds).subquery()
    ) if conds else select(func.count(TTSOrder.order_id))
    total = int((await db.execute(count_q)).scalar_one())

    base = (
        base.order_by(TTSOrder.update_time_tt.desc().nullslast(), TTSOrder.order_id.desc())
        .limit(limit)
        .offset(offset)
    )

    rows = (await db.execute(base)).all()
    items: list[OrderListItem] = []
    for row in rows:
        buyer_email_val, buyer_tt_id_val, _, settlement_val = _extract_order_meta(
            row.TTSOrder
        )
        payload = row.TTSOrder.raw_payload if isinstance(row.TTSOrder.raw_payload, dict) else {}
        product_name_val = next(
            (
                v
                for v in [
                    payload.get("product_name"),
                    payload.get("item_title"),
                    row.product_name_hint,
                ]
                if isinstance(v, str) and v.strip()
            ),
            None,
        )
        items.append(
            OrderListItem(
                order_id=row.TTSOrder.order_id,
                shop_id=row.TTSOrder.shop_id,
                shop_name=row.shop_name,
                buyer_email=buyer_email_val,
                buyer_tt_id=buyer_tt_id_val,
                product_name=product_name_val,
                tt_settlement_status=settlement_val,
                status=row.TTSOrder.status,
                line_count=int(row.line_count or 0),
                total_amount=row.TTSOrder.total_amount,
                currency=row.TTSOrder.currency,
                create_time_tt=row.TTSOrder.create_time_tt,
                update_time_tt=row.TTSOrder.update_time_tt,
                pending_lines=int(row.pending_lines or 0),
                shipped_lines=int(row.shipped_lines or 0),
                failed_lines=int(row.failed_lines or 0),
            )
        )
    return OrderListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get(
    "/{order_id}",
    response_model=OrderDetailResponse,
    summary="订单详情（含子单 + fulfillment 日志）",
    dependencies=[Depends(require_permission("orders:read"))],
)
async def order_detail(
    order_id: str = Path(..., max_length=64),
    db: AsyncSession = Depends(get_db),
) -> OrderDetailResponse:
    row = (
        await db.execute(
            select(TTSOrder, TTSShop.shop_name)
            .outerjoin(TTSShop, TTSShop.shop_id == TTSOrder.shop_id)
            .where(TTSOrder.order_id == order_id)
        )
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="order not found")
    order: TTSOrder = row.TTSOrder

    lines = (
        (
            await db.execute(
                select(TTSOrderLine)
                .where(TTSOrderLine.order_id == order_id)
                .order_by(TTSOrderLine.order_line_id)
            )
        )
        .scalars()
        .all()
    )

    logs = (
        (
            await db.execute(
                select(FulfillmentLog)
                .where(FulfillmentLog.order_id == order_id)
                .order_by(FulfillmentLog.id.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )

    return OrderDetailResponse(
        order_id=order.order_id,
        shop_id=order.shop_id,
        shop_name=row.shop_name,
        status=order.status,
        total_amount=order.total_amount,
        currency=order.currency,
        create_time_tt=order.create_time_tt,
        update_time_tt=order.update_time_tt,
        sla_deadline=order.sla_deadline.isoformat() if order.sla_deadline else None,
        lines=[
            OrderLineDetail(
                order_line_id=ln.order_line_id,
                seller_sku=ln.seller_sku,
                tts_product_id=ln.tts_product_id,
                tts_sku_id=ln.tts_sku_id,
                quantity=ln.quantity,
                unit_price=ln.unit_price,
                currency=ln.currency,
                status=ln.status,
                redeemed_code_id=ln.redeemed_code_id,
                redeemed_at=ln.redeemed_at.isoformat() if ln.redeemed_at else None,
                failed_reason=ln.failed_reason,
            )
            for ln in lines
        ],
        fulfillment_logs=[
            FulfillmentLogItem(
                id=log.id,
                api_endpoint=log.api_endpoint,
                api_method=log.api_method,
                http_status=log.http_status,
                api_code=log.api_code,
                api_message=log.api_message,
                request_id=log.request_id,
                latency_ms=log.latency_ms,
                error=log.error,
                created_at=log.created_at.isoformat(),
                order_line_id=log.order_line_id,
            )
            for log in logs
        ],
    )


@router.post(
    "/{order_id}/reship",
    response_model=ReshipResponse,
    summary="手动触发 fulfill_order 重发（幂等）",
    dependencies=[Depends(require_permission("orders:reship"))],
)
async def reship_order(
    request: Request,
    order_id: str = Path(..., max_length=64),
    body: ReshipRequest = ReshipRequest(),
    db: AsyncSession = Depends(get_db),
    arq: ArqRedis = Depends(get_arq_pool),
    operator: str = Depends(verify_admin_session),
) -> ReshipResponse:
    order = (
        await db.execute(select(TTSOrder).where(TTSOrder.order_id == order_id))
    ).scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="order not found")

    dedup_key = f"admin-reship:{order_id}:{uuid.uuid4().hex[:12]}"
    event = WebhookEvent(
        dedup_key=dedup_key,
        event_type="ADMIN_RESHIP",
        shop_id=order.shop_id,
        order_id=order_id,
        raw_payload={
            "source": "admin_reship_v2",
            "operator": operator,
            "order_id": order_id,
            "shop_id": order.shop_id,
            "note": body.note,
        },
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)

    job = await arq.enqueue_job("fulfill_order", event.id)
    job_id = job.job_id if job is not None else "unknown"

    await write_audit(
        db,
        operator=operator,
        action="reship",
        request=request,
        target_type="order",
        target_id=order_id,
        extra={"event_id": event.id, "job_id": job_id, "note": body.note},
    )

    logger.info(
        "admin reship (cookie) op=%s order=%s event=%d job=%s",
        operator,
        order_id,
        event.id,
        job_id,
    )

    return ReshipResponse(job_id=job_id, event_id=event.id, order_id=order_id)


@router.post(
    "/{order_id}/lines/{order_line_id}/manual-code",
    response_model=ManualCodeReshipResponse,
    summary="手动输入 CDK 并直接补发单个子单",
    dependencies=[Depends(require_permission("orders:reship"))],
)
async def manual_code_reship(
    request: Request,
    order_id: str = Path(..., max_length=64),
    order_line_id: str = Path(..., max_length=64),
    body: ManualCodeReshipRequest = ...,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    redis=Depends(get_redis),
    operator: str = Depends(verify_admin_session),
) -> ManualCodeReshipResponse:
    plaintext = body.plaintext_code.strip()
    if not plaintext:
        raise HTTPException(status_code=422, detail="CDK 不能为空")

    async with order_lock(redis, order_id):
        row = (
            await db.execute(
                select(TTSOrder, TTSShop, TTSOrderLine)
                .join(TTSShop, TTSShop.shop_id == TTSOrder.shop_id)
                .join(TTSOrderLine, TTSOrderLine.order_id == TTSOrder.order_id)
                .where(
                    TTSOrder.order_id == order_id,
                    TTSOrderLine.order_line_id == order_line_id,
                )
            )
        ).first()
        if not row:
            raise HTTPException(status_code=404, detail="order line not found")

        order: TTSOrder = row.TTSOrder
        shop: TTSShop = row.TTSShop
        line: TTSOrderLine = row.TTSOrderLine

        if line.status == "shipped":
            raise HTTPException(status_code=409, detail="子单已经发货，不能手动补码")
        if line.status in {"cancelled", "refunded"}:
            raise HTTPException(status_code=409, detail="子单已取消/退款，不能手动补码")
        if line.redeemed_code_id is not None:
            raise HTTPException(
                status_code=409,
                detail="子单已经绑定卡密，请先确认现有履约状态后再处理",
            )
        if order.status in {"CANCELLED", "RETURN"}:
            raise HTTPException(status_code=409, detail="订单已取消/退货，不能手动补码")

        mapping = await find_mapping_for_line(db, line)
        if mapping is None:
            raise HTTPException(status_code=409, detail="SKU 未匹配，先完成 SKU 映射")
        if mapping.inventory_locked:
            raise HTTPException(
                status_code=409,
                detail=f"该 SKU 已锁库存：{mapping.inventory_lock_reason or '未填写原因'}",
            )

        try:
            code = await code_inventory.import_locked_code(
                db,
                sku_id=mapping.internal_sku_id,
                face_value=mapping.face_value,
                plaintext=plaintext,
                order_line_id=line.order_line_id,
                operator=operator,
            )
            line.redeemed_code_id = code.id
            line.status = "pending"
            line.failed_reason = None
            await db.commit()
            await db.refresh(code)
            code_id = int(code.id)
            shop_id = shop.shop_id
            shop_cipher = shop.shop_cipher
        except IntegrityError as e:
            await db.rollback()
            raise HTTPException(
                status_code=409,
                detail="这张 CDK 已经导入或使用过，不能重复补发",
            ) from e

        return await _send_manual_code_reship(
            request=request,
            db=db,
            settings=settings,
            redis=redis,
            operator=operator,
            order_id=order_id,
            order_line_id=order_line_id,
            shop_id=shop_id,
            shop_cipher=shop_cipher,
            code_id=code_id,
            plaintext=plaintext,
            note=body.note,
        )
