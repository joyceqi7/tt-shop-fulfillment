"""统一采购 API（批量入库等）。"""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_permission, verify_admin_session
from app.models.sku_mapping import SkuMapping
from app.models.supplier import Supplier
from app.models.supplier_sku_link import SupplierSkuLink
from app.services.admin_audit import write_audit
from app.services import procurement
from app.services.supplier_adapters.registry import get_adapter
from app.services.supplier_adapters.wgcards import build_wgcards_client
from app.services.wgcards_client import WGCardsAPIError, WGCardsTimeoutError


logger = logging.getLogger(__name__)


router = APIRouter(dependencies=[Depends(verify_admin_session)])


class BulkProcurementRequest(BaseModel):
    sku_mapping_id: int = Field(..., ge=1)
    supplier_id: int = Field(..., ge=1)
    quantity: int = Field(..., ge=1, le=100)
    unit_cost: int = Field(..., ge=0, description="进价单价（美分）")
    purchase_date: date
    note: str | None = None
    timeout_seconds: int = Field(default=300, ge=30, le=900)


class BulkProcurementResponse(BaseModel):
    purchase_order_id: int
    po_number: str
    supplier_order_id: int
    external_order_id: str
    batch_id: str
    cards_received: int
    cards_imported: int


@router.post(
    "/bulk",
    response_model=BulkProcurementResponse,
    status_code=201,
    dependencies=[Depends(require_permission("procurement:bulk"))],
)
async def bulk_procurement(
    request: Request,
    body: BulkProcurementRequest,
    db: AsyncSession = Depends(get_db),
    operator: str = Depends(verify_admin_session),
) -> BulkProcurementResponse:
    """API 批量采购入库：按 SKU 链接下单取卡并导入 free 库存。"""
    mapping = await db.get(SkuMapping, body.sku_mapping_id)
    if mapping is None or not mapping.active:
        raise HTTPException(status_code=404, detail="sku mapping not found or inactive")

    supplier = await db.get(Supplier, body.supplier_id)
    if supplier is None:
        raise HTTPException(status_code=404, detail="supplier not found")
    if supplier.supplier_type != "api":
        raise HTTPException(status_code=400, detail="supplier is not API type")
    if not supplier.enabled:
        raise HTTPException(status_code=409, detail="supplier is disabled")
    if get_adapter(supplier) is None:
        raise HTTPException(status_code=400, detail="supplier has no API adapter configured")

    link = (
        await db.execute(
            select(SupplierSkuLink).where(
                SupplierSkuLink.sku_mapping_id == body.sku_mapping_id,
                SupplierSkuLink.supplier_id == body.supplier_id,
            )
        )
    ).scalar_one_or_none()
    if link is None:
        raise HTTPException(
            status_code=400,
            detail="sku has no API link for this supplier; configure in SKU mapping first",
        )

    face = link.external_face_value
    # 下单前查 WGCards 目录，校验礼品卡类型并解析 faceValue（避免 -101）
    if supplier.is_wgcards:
        try:
            client = build_wgcards_client(supplier)
            catalog = await client.lookup_sku(
                link.external_sku_id,
                currency_code=supplier.api_currency or "CNY",
            )
        except WGCardsAPIError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        if catalog is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"WGCards 目录中找不到 SKU {link.external_sku_id}；"
                    "请在 SKU 映射里用「浏览商品」重新绑定礼品卡"
                ),
            )
        try:
            face = client.resolve_place_order_face_value(catalog, face)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    try:
        result = await procurement.bulk_to_inventory(
            db,
            supplier=supplier,
            operator=operator,
            internal_sku_id=mapping.internal_sku_id,
            face_value=mapping.face_value,
            external_sku_id=link.external_sku_id,
            external_face_value=face,
            quantity=body.quantity,
            unit_cost=body.unit_cost,
            purchase_date=body.purchase_date,
            note=body.note,
            timeout_seconds=body.timeout_seconds,
        )
    except (WGCardsAPIError, WGCardsTimeoutError) as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except IntegrityError as e:
        logger.exception("bulk_procurement integrity error")
        raise HTTPException(
            status_code=409,
            detail=(
                "数据库约束失败，可能未执行 migration 0007/0008。"
                f" 原始错误: {e.orig}"
            ),
        ) from e
    except SQLAlchemyError as e:
        logger.exception("bulk_procurement db error")
        raise HTTPException(status_code=502, detail=f"database error: {e}") from e
    except Exception as e:
        logger.exception(
            "bulk_procurement failed sku=%s supplier=%s",
            body.sku_mapping_id,
            body.supplier_id,
        )
        raise HTTPException(status_code=502, detail=f"Unexpected error: {e}") from e

    await write_audit(
        db,
        operator=operator,
        action="procurement_bulk",
        request=request,
        target_type="supplier_order",
        target_id=result.supplier_order_id,
        extra={
            "sku_mapping_id": body.sku_mapping_id,
            "supplier_id": body.supplier_id,
            "po_number": result.po_number,
            "cards_imported": result.cards_imported,
        },
        commit=False,
    )
    await db.commit()

    return BulkProcurementResponse(
        purchase_order_id=result.purchase_order_id,
        po_number=result.po_number,
        supplier_order_id=result.supplier_order_id,
        external_order_id=result.external_order_id,
        batch_id=result.batch_id,
        cards_received=result.cards_received,
        cards_imported=result.cards_imported,
    )
