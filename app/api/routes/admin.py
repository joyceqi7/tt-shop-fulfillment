"""管理 API（联调 / 应急用）。

路由：
- POST /admin/reship  手动触发一个订单的履约编排

鉴权：`X-Admin-Token` header 必须等于 Settings.admin_token。
未配置 `ADMIN_TOKEN` 环境变量时整组路由 503 直接禁用（生产上线前务必配置强密钥）。

实现细节：
- **不重写发货逻辑**：造一个虚拟 `webhook_event` 行（event_type='ADMIN_RESHIP'），
  入 arq 队列，复用 `fulfill_order` task + `fulfill_order_once` 服务。
- **幂等性由现有机制承担**：
  - 已 shipped 的 line 被 pending 过滤
  - 上次 redeem 失败留下的 locked code 被复用
  - 上次 no_stock / sku_not_mapped 的 line 在补了库存 / 映射后，这次能拿到
- 因此反复调用 reship 安全；**唯一的副作用**是生成新的 webhook_event 审计记录。
"""

from __future__ import annotations

import logging
import uuid

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_arq_pool, get_db, get_settings_dep
from app.config import Settings
from app.models.webhook_event import WebhookEvent


logger = logging.getLogger(__name__)


router = APIRouter()


class ReshipRequest(BaseModel):
    order_id: str = Field(min_length=1, max_length=64)
    shop_id: str | None = Field(default=None, max_length=64)


class ReshipResponse(BaseModel):
    job_id: str
    event_id: int
    order_id: str
    status: str = "queued"


async def verify_admin(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    settings: Settings = Depends(get_settings_dep),
) -> None:
    """Admin 鉴权依赖。"""
    if not settings.admin_token:
        # 未配置 ADMIN_TOKEN → 整组路由等同禁用
        raise HTTPException(
            status_code=503, detail="admin endpoints disabled (ADMIN_TOKEN not configured)"
        )
    if not x_admin_token or x_admin_token != settings.admin_token:
        raise HTTPException(status_code=403, detail="invalid admin token")


@router.post(
    "/reship",
    response_model=ReshipResponse,
    dependencies=[Depends(verify_admin)],
)
async def reship(
    req: ReshipRequest,
    db: AsyncSession = Depends(get_db),
    arq: ArqRedis = Depends(get_arq_pool),
) -> ReshipResponse:
    dedup_key = f"admin-reship:{req.order_id}:{uuid.uuid4().hex[:12]}"
    event = WebhookEvent(
        dedup_key=dedup_key,
        event_type="ADMIN_RESHIP",
        shop_id=req.shop_id,
        order_id=req.order_id,
        raw_payload={
            "source": "admin_reship",
            "order_id": req.order_id,
            "shop_id": req.shop_id,
        },
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)

    job = await arq.enqueue_job("fulfill_order", event.id)
    job_id = job.job_id if job is not None else "unknown"

    logger.info(
        "admin reship event_id=%s order=%s shop=%s job_id=%s",
        event.id,
        req.order_id,
        req.shop_id,
        job_id,
    )

    return ReshipResponse(job_id=job_id, event_id=event.id, order_id=req.order_id)
