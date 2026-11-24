"""WGCards SupplierAdapter 实现。"""

from __future__ import annotations

import json
import logging
import time

from app.models.supplier import Supplier
from app.services.supplier_adapters.base import AcquireResult
from app.services.wgcards_client import (
    WGCardsAPIError,
    WGCardsCard,
    WGCardsClient,
)

logger = logging.getLogger(__name__)


def build_wgcards_client(supplier: Supplier) -> WGCardsClient:
    if not supplier.is_wgcards:
        raise ValueError(f"supplier {supplier.id} is not API-enabled (WGCards)")
    host = (supplier.api_host or "").strip()
    if not host:
        raise ValueError(f"supplier {supplier.id} api_host not configured")
    return WGCardsClient(
        host=host,
        app_id=supplier.api_app_id,
        account_id=supplier.api_account_id,
        app_key=supplier.api_app_key,
    )


def format_wgcards_card(card: WGCardsCard) -> str:
    plain = card.card.strip()
    pin = (card.pin_code or "").strip()
    if pin:
        return f"{plain}:{pin}"
    return plain


def cards_to_plaintexts(cards: list[WGCardsCard]) -> list[str]:
    return [format_wgcards_card(c) for c in cards if c.card.strip()]


class WGCardsAdapter:
    adapter_key = "wgcards"

    def __init__(self, supplier: Supplier):
        self._supplier = supplier
        self._client = build_wgcards_client(supplier)

    async def place_and_fetch(
        self,
        *,
        service_order: str,
        external_sku_id: str,
        buy_num: int,
        face_value: float | None,
        currency: str,
        timeout_seconds: int = 300,
        poll_interval: int = 5,
    ) -> AcquireResult:
        t0 = time.monotonic()
        result = await self._client.place_order(
            service_order=service_order,
            sku_id=external_sku_id,
            buy_num=buy_num,
            face_value=face_value,
            currency=currency,
        )
        if not result.success or not result.order_id:
            msg = result.message or "place_order failed"
            logger.warning(
                "WGCards place_order failed sku=%s service_order=%s: %s",
                external_sku_id,
                service_order,
                msg,
            )
            raise WGCardsAPIError(msg)

        external_order_id = result.order_id
        cards = await self._client.poll_and_fetch_cards(
            external_order_id,
            timeout_seconds=timeout_seconds,
            poll_interval=poll_interval,
        )
        plaintexts = cards_to_plaintexts(cards)
        elapsed = time.monotonic() - t0

        total_amount: float | None = None
        if cards:
            # WGCards 无单笔汇总时由调用方按 PO 填写；此处留空
            pass

        return AcquireResult(
            external_order_id=external_order_id,
            plaintexts=plaintexts,
            total_amount=total_amount,
            currency=currency,
            raw_response=json.dumps(
                {
                    "adapter": self.adapter_key,
                    "external_order_id": external_order_id,
                    "card_count": len(cards),
                    "elapsed_seconds": elapsed,
                },
                ensure_ascii=False,
            ),
        )
