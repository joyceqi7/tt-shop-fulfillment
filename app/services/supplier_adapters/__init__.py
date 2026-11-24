"""供应商 API 适配层（内部用，Admin 不暴露 adapter 概念）。"""

from app.services.supplier_adapters.base import AcquireResult
from app.services.supplier_adapters.registry import get_adapter

__all__ = ["AcquireResult", "get_adapter"]
