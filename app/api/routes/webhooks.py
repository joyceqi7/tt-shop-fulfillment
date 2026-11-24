"""TT Shop webhook 接收器。

关键约束（plan §三 + Onboarding §2.4.3）：
- 必须在 5 秒内返回 200（TT 平台超时即重发）
- 必须幂等（TT 可能重发同一事件）
- 验签失败 → 403，不入库
- 入库成功 → enqueue 到 arq → 立即返回

幂等键 dedup_key：
- 优先 `tts_notification_id`（TT 应该会带，待联调确认）
- 没有 → SHA-1(raw_body)
"""

from __future__ import annotations

import hashlib
import json
import logging

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_arq_pool, get_db, get_settings_dep
from app.auth import webhook_signer
from app.config import Settings
from app.models.webhook_event import WebhookEvent


logger = logging.getLogger(__name__)
router = APIRouter()


def _compute_dedup_key(payload: dict, body: bytes) -> str:
    return (
        payload.get("tts_notification_id")
        or payload.get("notification_id")
        or hashlib.sha1(body).hexdigest()
    )


def _select_tts_signature(
    x_tt_signature: str | None,
    x_tts_signature: str | None,
) -> tuple[str | None, str | None]:
    """Return the TT webhook signature and the header name it came from."""
    if x_tt_signature:
        return x_tt_signature, "X-Tt-Signature"
    if x_tts_signature:
        return x_tts_signature, "X-Tts-Signature"
    return None, None


@router.post("/tts")
async def receive_tts_webhook(
    request: Request,
    x_tt_signature: str | None = Header(default=None, alias="X-Tt-Signature"),
    x_tts_signature: str | None = Header(default=None, alias="X-Tts-Signature"),
    settings: Settings = Depends(get_settings_dep),
    db: AsyncSession = Depends(get_db),
    arq: ArqRedis = Depends(get_arq_pool),
) -> dict[str, object]:
    body = await request.body()
    signature, signature_header = _select_tts_signature(
        x_tt_signature,
        x_tts_signature,
    )

    if not settings.webhook_verify_disabled:
        if not webhook_signer.verify(body, signature, settings.tts_app_secret):
            logger.warning(
                "webhook signature verification failed header=%s sig_present=%s body_len=%d",
                signature_header,
                bool(signature),
                len(body),
            )
            raise HTTPException(status_code=403, detail="invalid signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid json body")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="payload must be a JSON object")

    dedup_key = _compute_dedup_key(payload, body)
    headers_dict = {k: v for k, v in request.headers.items()}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}

    stmt = (
        pg_insert(WebhookEvent)
        .values(
            dedup_key=dedup_key,
            event_type=payload.get("type") or payload.get("event_type"),
            tts_notification_id=payload.get("tts_notification_id")
            or payload.get("notification_id"),
            shop_id=payload.get("shop_id") or data.get("shop_id"),
            order_id=data.get("order_id") or payload.get("order_id"),
            timestamp_tt=payload.get("timestamp"),
            raw_payload=payload,
            raw_headers=headers_dict,
            signature=signature,
            status="received",
        )
        .on_conflict_do_nothing(index_elements=["dedup_key"])
        .returning(WebhookEvent.id)
    )
    inserted_id = (await db.execute(stmt)).scalar_one_or_none()
    await db.commit()

    if inserted_id is None:
        logger.info("webhook duplicate ignored dedup_key=%s", dedup_key)
        return {"code": 0, "message": "duplicate", "dedup_key": dedup_key}

    await arq.enqueue_job("fulfill_order", inserted_id)
    logger.info(
        "webhook accepted event_id=%s order=%s type=%s",
        inserted_id,
        data.get("order_id"),
        payload.get("type"),
    )
    return {"code": 0, "message": "queued", "event_id": inserted_id}
