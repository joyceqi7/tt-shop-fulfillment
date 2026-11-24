"""采购单辅助函数。"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.purchase_order import PurchaseOrder


async def next_po_number(db: AsyncSession) -> str:
    today = date.today().strftime("%Y%m%d")
    prefix = f"PO{today}-"
    n = (
        await db.execute(
            select(func.count(PurchaseOrder.id)).where(
                PurchaseOrder.po_number.like(f"{prefix}%")
            )
        )
    ).scalar_one()
    return f"{prefix}{(int(n or 0) + 1):03d}"
