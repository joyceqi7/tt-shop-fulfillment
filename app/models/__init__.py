"""所有 ORM 模型的集中导出，alembic env 会 import 这里拿到 metadata。"""

from app.models.audit_log import AdminAuditLog
from app.models.code import CODE_STATUSES, Code
from app.models.fulfillment_log import FulfillmentLog
from app.models.order import (
    ORDER_LINE_STATUSES,
    ORDER_STATUSES,
    TTSOrder,
    TTSOrderLine,
)
from app.models.purchase_order import PO_PAYMENT_STATUSES, PurchaseOrder
from app.models.shop import TTSShop
from app.models.sku_mapping import SkuMapping
from app.models.supplier import Supplier
from app.models.supplier_order import SupplierOrder
from app.models.supplier_sku_link import SupplierSkuLink
from app.models.token import TTSToken
from app.models.tts_product import TTSProduct, TTSProductSku
from app.models.webhook_event import WEBHOOK_STATUSES, WebhookEvent

__all__ = [
    "AdminAuditLog",
    "CODE_STATUSES",
    "Code",
    "FulfillmentLog",
    "ORDER_LINE_STATUSES",
    "ORDER_STATUSES",
    "PO_PAYMENT_STATUSES",
    "PurchaseOrder",
    "SkuMapping",
    "Supplier",
    "SupplierOrder",
    "SupplierSkuLink",
    "TTSOrder",
    "TTSOrderLine",
    "TTSProduct",
    "TTSProductSku",
    "TTSShop",
    "TTSToken",
    "WEBHOOK_STATUSES",
    "WebhookEvent",
]
