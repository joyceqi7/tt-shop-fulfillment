"""SupplierAdapter 协议。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True)
class AcquireResult:
    external_order_id: str
    plaintexts: list[str]
    total_amount: float | None
    currency: str | None
    raw_response: str | None


class SupplierAdapter(Protocol):
    adapter_key: str

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
    ) -> AcquireResult: ...
