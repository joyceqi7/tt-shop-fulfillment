"""Adapter 注册表。"""

from __future__ import annotations

from app.models.supplier import Supplier
from app.services.supplier_adapters.base import SupplierAdapter
from app.services.supplier_adapters.wgcards import WGCardsAdapter


def get_adapter(supplier: Supplier) -> SupplierAdapter | None:
    """根据 supplier 配置返回适配器。"""
    if supplier.supplier_type != "api" or not supplier.enabled:
        return None
    key = supplier.adapter_key or "wgcards"
    if key == "wgcards" and supplier.is_wgcards:
        return WGCardsAdapter(supplier)
    return None
