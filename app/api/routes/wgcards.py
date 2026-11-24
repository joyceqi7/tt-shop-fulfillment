"""WGCards API 路由。

提供供应商 API 测试、商品查询、下单、订单查询等功能。
"""

from __future__ import annotations

import logging
from typing import Any

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_permission, verify_admin_session
from app.models.supplier import Supplier
from app.services.admin_audit import write_audit
from app.services.wgcards_client import (
    WGCardsAPIError,
    WGCardsClient,
    WGCardsTimeoutError,
)
from app.services.supplier_adapters.registry import get_adapter
from app.services.wgcards_procurement import bulk_purchase_to_inventory


logger = logging.getLogger(__name__)


router = APIRouter(dependencies=[Depends(verify_admin_session)])


# ============== Schemas ==============


class WGCardsConfig(BaseModel):
    """WGCards 配置。"""
    app_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    app_key: str = Field(min_length=1)
    host: str = Field(default="")
    currency: str = Field(default="USD")


class WGCardsAccountInfo(BaseModel):
    """账户信息响应。"""
    wallet_id: str
    currency: str
    balance: float
    effective: bool


class WGCardsStockInfo(BaseModel):
    """库存信息。"""
    item_id: str
    sku_id: str
    number: int  # -1 表示无限制


class WGCardsPlaceOrderRequest(BaseModel):
    """下单请求。"""
    service_order: str = Field(min_length=1)
    sku_id: str = Field(min_length=1)
    buy_num: int = Field(ge=1, le=100)
    face_value: float | None = None
    currency: str = Field(default="USD")


class WGCardsPlaceOrderResponse(BaseModel):
    """下单响应。"""
    success: bool
    order_id: str | None
    message: str
    service_order: str | None = None


class WGCardsOrderDetail(BaseModel):
    """订单详情。"""
    order_id: str
    request_id: str
    order_status: int
    delivery_status: int
    order_way: int
    create_time: str
    total_amount: float
    cur: str
    lines: list[dict[str, Any]]


class WGCardsCard(BaseModel):
    """卡密信息。"""
    sku_id: str
    card: str
    pin_code: str
    sn_code: str


class WGCardsBulkPurchaseRequest(BaseModel):
    """WGCards 批量采购入库。"""
    internal_sku_id: str = Field(min_length=1, max_length=64)
    face_value: int = Field(..., ge=0, description="内部 SKU 面额（美分）")
    wgcards_sku_id: str = Field(min_length=1, max_length=64)
    wgcards_face_value: float | None = Field(default=None, ge=0)
    quantity: int = Field(..., ge=1, le=100)
    unit_cost: int = Field(..., ge=0, description="进价单价（美分）")
    purchase_date: date
    note: str | None = None
    timeout_seconds: int = Field(default=300, ge=30, le=600)


class WGCardsBulkPurchaseResponse(BaseModel):
    purchase_order_id: int
    po_number: str
    supplier_order_id: int
    wgcards_order_id: str
    batch_id: str
    cards_received: int
    cards_imported: int


# ============== 辅助函数 ==============


async def _get_wgcards_client(
    db: AsyncSession, supplier_id: int
) -> tuple[WGCardsClient, Supplier]:
    """根据 supplier_id 创建 WGCards 客户端。"""
    supplier = (
        await db.execute(select(Supplier).where(Supplier.id == supplier_id))
    ).scalar_one_or_none()

    if not supplier:
        raise HTTPException(status_code=404, detail="supplier not found")

    if supplier.supplier_type != "api":
        raise HTTPException(
            status_code=400,
            detail="供货商类型不是 API，请在编辑页选择「API 供应商」",
        )

    missing = [
        name
        for name, val in (
            ("App ID", supplier.api_app_id),
            ("Account ID", supplier.api_account_id),
            ("App Key", supplier.api_app_key),
        )
        if not (val and str(val).strip())
    ]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"API 凭证不完整，缺少: {', '.join(missing)}",
        )

    host = (supplier.api_host or "").strip()
    if not host:
        raise HTTPException(
            status_code=400,
            detail="API Host 未配置，请在供货商编辑页填写",
        )

    client = WGCardsClient(
        host=host,
        app_id=str(supplier.api_app_id).strip(),
        account_id=str(supplier.api_account_id).strip(),
        app_key=str(supplier.api_app_key).strip(),
    )
    return client, supplier


# ============== Routes ==============


@router.post(
    "/suppliers/{supplier_id}/wgcards/test-connection",
    dependencies=[Depends(require_permission("suppliers:credentials"))],
)
async def test_wgcards_connection(
    supplier_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """测试 WGCards 连接。"""
    try:
        client, _ = await _get_wgcards_client(db, supplier_id)
        token = await client.get_token()
        return {
            "success": True,
            "message": "Connection successful",
            "token_preview": token[:20] + "..." if len(token) > 20 else token,
        }
    except WGCardsAPIError as e:
        return {"success": False, "message": str(e)}
    except Exception as e:
        return {"success": False, "message": f"Unexpected error: {str(e)}"}


@router.get(
    "/suppliers/{supplier_id}/wgcards/account",
    response_model=WGCardsAccountInfo,
    dependencies=[Depends(require_permission("wgcards:catalog"))],
)
async def get_wgcards_account(
    supplier_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
) -> WGCardsAccountInfo:
    """获取 WGCards 账户信息（余额）。"""
    try:
        client, _ = await _get_wgcards_client(db, supplier_id)
        account = await client.get_account()
        return WGCardsAccountInfo(
            wallet_id=account.wallet_id,
            currency=account.currency,
            balance=account.balance,
            effective=account.effective,
        )
    except HTTPException:
        raise
    except WGCardsAPIError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        logger.exception("get_wgcards_account failed supplier_id=%s", supplier_id)
        raise HTTPException(status_code=502, detail=f"Unexpected error: {e}") from e


@router.get(
    "/suppliers/{supplier_id}/wgcards/items",
    dependencies=[Depends(require_permission("wgcards:catalog"))],
)
async def get_wgcards_items(
    supplier_id: int = Path(..., ge=1),
    currency_code: str | None = Query(default=None),
    language: str = Query(default="zh"),
    current: int = Query(default=1, ge=1),
    size: int = Query(default=10, ge=1, le=100),
    item_id: str = Query(default=""),
    item_name: str = Query(default=""),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """获取商品列表（分页）。"""
    try:
        client, supplier = await _get_wgcards_client(db, supplier_id)
        currency = currency_code or supplier.api_currency or "CNY"
        result = await client.get_item(
            currency_code=currency,
            language=language,
            current=current,
            size=size,
            item_id=item_id,
            item_name=item_name,
        )
        return result
    except HTTPException:
        raise
    except WGCardsAPIError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        logger.exception("get_wgcards_items failed supplier_id=%s", supplier_id)
        raise HTTPException(status_code=502, detail=f"Unexpected error: {e}") from e


def _flatten_catalog_items(
    records: list[Any],
    *,
    spu_type: int | None = None,
    gift_card_only: bool = False,
) -> list[dict[str, Any]]:
    """将 getItem records 展平为 SKU 行。"""
    rows: list[dict[str, Any]] = []
    for item in records:
        if not isinstance(item, dict):
            continue
        item_spu = item.get("spuType")
        if spu_type is not None and item_spu != spu_type:
            continue
        if gift_card_only and item_spu != 2:
            continue
        base = {
            "itemId": item.get("itemId"),
            "itemName": item.get("itemName"),
            "itemBrandName": item.get("itemBrandName"),
            "currencyCode": item.get("currencyCode"),
            "spuType": item_spu,
            "description": item.get("description"),
        }
        for sku in item.get("skus") or []:
            if not isinstance(sku, dict):
                continue
            rows.append(
                {
                    **base,
                    "skuId": sku.get("skuId"),
                    "skuName": sku.get("skuName"),
                    "minFaceValue": sku.get("minFaceValue"),
                    "maxFaceValue": sku.get("maxFaceValue"),
                    "skuPrice": sku.get("skuPrice"),
                    "skuPriceCurrency": sku.get("skuPriceCurrency"),
                    "stock": sku.get("stock"),
                }
            )
    return rows


@router.get(
    "/suppliers/{supplier_id}/wgcards/catalog",
    dependencies=[Depends(require_permission("wgcards:catalog"))],
)
async def get_wgcards_catalog(
    supplier_id: int = Path(..., ge=1),
    q: str = Query(default="", description="商品名或 SKU ID"),
    spu_type: int | None = Query(default=None, alias="spu_type"),
    gift_card_only: bool = Query(default=False),
    current: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """API 供应商商品目录（全字段展平 + SKU/商品名检索）。"""
    try:
        client, supplier = await _get_wgcards_client(db, supplier_id)
        currency = supplier.api_currency or "CNY"
        query = q.strip()

        if query.isdigit():
            catalog = await client.lookup_sku(
                query, currency_code=currency, include_item_id=True
            )
            if catalog:
                rows = _flatten_catalog_items(
                    [catalog],
                    spu_type=spu_type,
                    gift_card_only=gift_card_only,
                )
                return {
                    "current": 1,
                    "pages": 1,
                    "total": len(rows),
                    "records": rows,
                }

        if query.isdigit():
            page = await client.get_item_and_stock(
                currency_code=currency,
                current=current,
                size=size,
                item_id=query,
            )
        else:
            page = await client.get_item(
                currency_code=currency,
                language="zh",
                current=current,
                size=size,
                item_name=query,
            )
        records = page.get("records") or []
        rows = _flatten_catalog_items(
            records,
            spu_type=spu_type,
            gift_card_only=gift_card_only,
        )
        return {
            "current": page.get("current", current),
            "pages": page.get("pages", 1),
            "total": page.get("total", len(rows)),
            "records": rows,
        }
    except HTTPException:
        raise
    except WGCardsAPIError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        logger.exception("get_wgcards_catalog failed supplier_id=%s", supplier_id)
        raise HTTPException(status_code=502, detail=f"Unexpected error: {e}") from e


@router.post(
    "/suppliers/{supplier_id}/wgcards/stock",
    dependencies=[Depends(require_permission("wgcards:catalog"))],
)
async def get_wgcards_stock(
    supplier_id: int = Path(..., ge=1),
    sku_ids: list[str] = [],
    db: AsyncSession = Depends(get_db),
) -> list[WGCardsStockInfo]:
    """获取 SKU 库存。"""
    if not sku_ids:
        raise HTTPException(status_code=400, detail="sku_ids required")

    try:
        client, _ = await _get_wgcards_client(db, supplier_id)
        stocks = await client.get_stock(sku_ids)
        return [
            WGCardsStockInfo(
                item_id=stock.item_id,
                sku_id=stock.sku_id,
                number=stock.number,
            )
            for stock in stocks
        ]
    except WGCardsAPIError as e:
        raise HTTPException(status_code=502, detail=f"WGCards API error: {str(e)}")


@router.post(
    "/suppliers/{supplier_id}/wgcards/order",
    response_model=WGCardsPlaceOrderResponse,
    dependencies=[Depends(require_permission("procurement:bulk"))],
)
async def place_wgcards_order(
    supplier_id: int = Path(..., ge=1),
    body: WGCardsPlaceOrderRequest = ...,
    db: AsyncSession = Depends(get_db),
) -> WGCardsPlaceOrderResponse:
    """下单购买礼品卡（仅供测试，生产环境应通过履约流程调用）。"""
    try:
        client, _ = await _get_wgcards_client(db, supplier_id)
        result = await client.place_order(
            service_order=body.service_order,
            sku_id=body.sku_id,
            buy_num=body.buy_num,
            face_value=body.face_value,
            currency=body.currency,
        )
        return WGCardsPlaceOrderResponse(
            success=result.success,
            order_id=result.order_id,
            message=result.message,
        )
    except WGCardsAPIError as e:
        raise HTTPException(status_code=502, detail=f"WGCards API error: {str(e)}")


@router.get(
    "/suppliers/{supplier_id}/wgcards/orders",
    dependencies=[Depends(require_permission("supplier_orders:read"))],
)
async def get_wgcards_orders(
    supplier_id: int = Path(..., ge=1),
    current: int = Query(default=1, ge=1),
    size: int = Query(default=10, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """获取历史订单列表。"""
    try:
        client, _ = await _get_wgcards_client(db, supplier_id)
        result = await client.get_order_info(current=current, size=size)
        return result
    except WGCardsAPIError as e:
        raise HTTPException(status_code=502, detail=f"WGCards API error: {str(e)}")


@router.get(
    "/suppliers/{supplier_id}/wgcards/orders/{order_id}",
    response_model=WGCardsOrderDetail,
    dependencies=[Depends(require_permission("supplier_orders:read"))],
)
async def get_wgcards_order_detail(
    supplier_id: int = Path(..., ge=1),
    order_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
) -> WGCardsOrderDetail:
    """获取订单详情。"""
    try:
        client, _ = await _get_wgcards_client(db, supplier_id)
        order, lines = await client.get_order_detail(order_id)
        return WGCardsOrderDetail(
            order_id=order.order_id,
            request_id=order.request_id,
            order_status=order.order_status,
            delivery_status=order.delivery_status,
            order_way=order.order_way,
            create_time=order.create_time,
            total_amount=order.total_amount,
            cur=order.cur,
            lines=[
                {
                    "sku_id": line.sku_id,
                    "sku_name": line.sku_name,
                    "spu_name": line.spu_name,
                    "is_end": line.is_end,
                    "buy_num": line.buy_num,
                    "face_value": line.face_value,
                    "send_out_num": line.send_out_num,
                    "actual_paid_price": line.actual_paid_price,
                    "actual_paid_price_cur": line.actual_paid_price_cur,
                }
                for line in lines
            ],
        )
    except WGCardsAPIError as e:
        raise HTTPException(status_code=502, detail=f"WGCards API error: {str(e)}")


@router.get(
    "/suppliers/{supplier_id}/wgcards/orders/{order_id}/cards",
    response_model=list[WGCardsCard],
    dependencies=[Depends(require_permission("procurement:bulk"))],
)
async def get_wgcards_cards(
    supplier_id: int = Path(..., ge=1),
    order_id: str = Path(...),
    current: int = Query(default=1, ge=1),
    size: int = Query(default=200, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
) -> list[WGCardsCard]:
    """获取订单卡密。"""
    try:
        client, _ = await _get_wgcards_client(db, supplier_id)
        cards = await client.get_buy_card(order_id, current=current, size=size)
        return [
            WGCardsCard(
                sku_id=card.sku_id,
                card=card.card,
                pin_code=card.pin_code,
                sn_code=card.sn_code,
            )
            for card in cards
        ]
    except WGCardsAPIError as e:
        raise HTTPException(status_code=502, detail=f"WGCards API error: {str(e)}")


@router.post(
    "/suppliers/{supplier_id}/wgcards/bulk-purchase",
    response_model=WGCardsBulkPurchaseResponse,
    status_code=201,
    dependencies=[Depends(require_permission("procurement:bulk"))],
)
async def wgcards_bulk_purchase(
    request: Request,
    supplier_id: int = Path(..., ge=1),
    body: WGCardsBulkPurchaseRequest = ...,
    db: AsyncSession = Depends(get_db),
    operator: str = Depends(verify_admin_session),
) -> WGCardsBulkPurchaseResponse:
    """WGCards 批量采购：下单取卡并自动导入库存。"""
    supplier = (
        await db.execute(select(Supplier).where(Supplier.id == supplier_id))
    ).scalar_one_or_none()
    if not supplier:
        raise HTTPException(status_code=404, detail="supplier not found")
    if not supplier.enabled:
        raise HTTPException(status_code=409, detail="supplier is disabled")
    if supplier.supplier_type != "api":
        raise HTTPException(status_code=400, detail="supplier is not API type")
    if get_adapter(supplier) is None:
        raise HTTPException(
            status_code=400,
            detail="supplier has no API adapter configured",
        )

    try:
        result = await bulk_purchase_to_inventory(
            db,
            supplier=supplier,
            operator=operator,
            internal_sku_id=body.internal_sku_id,
            face_value=body.face_value,
            wgcards_sku_id=body.wgcards_sku_id,
            wgcards_face_value=body.wgcards_face_value,
            quantity=body.quantity,
            unit_cost=body.unit_cost,
            purchase_date=body.purchase_date,
            note=body.note,
            timeout_seconds=body.timeout_seconds,
        )
    except (WGCardsAPIError, WGCardsTimeoutError) as e:
        raise HTTPException(status_code=502, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await write_audit(
        db,
        operator=operator,
        action="wgcards_bulk_purchase",
        request=request,
        target_type="supplier_order",
        target_id=result.supplier_order_id,
        extra={
            "supplier_id": supplier_id,
            "po_number": result.po_number,
            "wgcards_order_id": result.wgcards_order_id,
            "cards_imported": result.cards_imported,
        },
        commit=False,
    )
    await db.commit()

    return WGCardsBulkPurchaseResponse(
        purchase_order_id=result.purchase_order_id,
        po_number=result.po_number,
        supplier_order_id=result.supplier_order_id,
        wgcards_order_id=result.wgcards_order_id,
        batch_id=result.batch_id,
        cards_received=result.cards_received,
        cards_imported=result.cards_imported,
    )


@router.post(
    "/suppliers/{supplier_id}/wgcards/orders/{order_id}/wait",
    dependencies=[Depends(require_permission("procurement:bulk"))],
)
async def wait_for_wgcards_delivery(
    supplier_id: int = Path(..., ge=1),
    order_id: str = Path(...),
    timeout_seconds: int = Query(default=300, ge=10, le=600),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """轮询等待订单发货完成（仅供测试）。"""
    try:
        client, _ = await _get_wgcards_client(db, supplier_id)
        order = await client.wait_for_delivery(order_id, timeout_seconds=timeout_seconds)
        return {
            "success": True,
            "order_id": order.order_id,
            "delivery_status": order.delivery_status,
            "message": "Delivery completed",
        }
    except WGCardsTimeoutError as e:
        raise HTTPException(status_code=408, detail=str(e))
    except WGCardsAPIError as e:
        raise HTTPException(status_code=502, detail=f"WGCards API error: {str(e)}")
