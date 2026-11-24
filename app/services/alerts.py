"""告警出口。

目前只支持飞书群机器人（webhook URL 形式，无签名）。
调用方 fire-and-forget——告警本身失败不要影响主流程，所以内部把异常吞了只记 log。

扩展点：钉钉、企微、PagerDuty 可再加适配器；先留一个接口。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings


logger = logging.getLogger(__name__)


async def send_feishu_text(text: str, *, timeout: float = 5.0) -> bool:
    """发送一条飞书群 text 消息。未配置 FEISHU_WEBHOOK_URL 直接 no-op。"""
    url = get_settings().feishu_webhook_url
    if not url:
        logger.debug("feishu webhook not configured; alert text=%r skipped", text)
        return False

    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}

    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as c:
            resp = await c.post(url, json=payload)
            if resp.status_code != 200:
                logger.warning(
                    "feishu alert HTTP %d, body=%r", resp.status_code, resp.text[:200]
                )
                return False
            data = resp.json()
            if data.get("code") not in (0, None):
                logger.warning("feishu alert code=%s msg=%r", data.get("code"), data)
                return False
            return True
    except Exception:
        logger.exception("feishu alert raised (swallowed)")
        return False


async def alert_dead_letter(
    *,
    event_id: int,
    order_id: str | None,
    shop_id: str | None,
    attempt: int,
    max_tries: int,
    error: str,
) -> None:
    """webhook event 重试耗尽进死信时的固定格式告警。"""
    text = (
        "[TT-Shop 死信告警]\n"
        f"env: {get_settings().app_env}\n"
        f"event_id: {event_id}\n"
        f"order_id: {order_id or '-'}\n"
        f"shop_id: {shop_id or '-'}\n"
        f"attempts: {attempt}/{max_tries}\n"
        f"last_error: {error[:500]}"
    )
    logger.error("dead letter alert: %s", text)
    await send_feishu_text(text)
