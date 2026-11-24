"""卡密库核心服务。

对外能力：
- `import_codes`  : 批量导入明文卡密（Fernet 加密 + SHA-256 去重）
- `lock_code`     : 为订单线取一张码（`FOR UPDATE SKIP LOCKED`）
- `mark_used`     : Redeem 回调成功后标记 used
- `release`       : CANCEL 时把 locked 的码退回 free
- `void`          : RETURN 且码已发出 → 作废
- `count_free`    : 水位监控

并发模型（plan §十 并发防线第 3 条）：
- 取码走 `SELECT id FROM codes WHERE status='free' AND sku_id=... AND face_value=...
  ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED`
- SKIP LOCKED 让多个 worker 在同一 SKU 队列里不会抢同一张码
- 加上部分索引 `ix_codes_pick WHERE status='free'`，取码 O(log N) 不扫全表
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.crypto import encrypt
from app.models.code import Code


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ImportResult:
    batch_id: str
    total: int
    inserted: int
    skipped_duplicate: int


def _hash_code(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


async def import_codes(
    session: AsyncSession,
    *,
    sku_id: str,
    face_value: int,
    plaintexts: list[str],
    operator: str,
    expired_at: datetime | None = None,
) -> ImportResult:
    """批量导入卡密。对重复的（code_hash 已存在）静默跳过。

    调用方需要在外部 commit。函数自身只 flush，便于和其他业务操作组合。
    """
    if not plaintexts:
        return ImportResult(batch_id="", total=0, inserted=0, skipped_duplicate=0)

    batch_id = str(uuid.uuid4())

    # 先在内存里去重，避免一次 import 里同码重复
    seen: set[str] = set()
    rows: list[dict] = []
    for raw in plaintexts:
        plain = raw.strip()
        if not plain:
            continue
        h = _hash_code(plain)
        if h in seen:
            continue
        seen.add(h)
        rows.append(
            {
                "batch_id": batch_id,
                "sku_id": sku_id,
                "face_value": face_value,
                "code_encrypted": encrypt(plain),
                "code_hash": h,
                "status": "free",
                "expired_at": expired_at,
                "operator": operator,
            }
        )

    if not rows:
        return ImportResult(batch_id=batch_id, total=0, inserted=0, skipped_duplicate=0)

    stmt = (
        pg_insert(Code)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["code_hash"])
        .returning(Code.id)
    )
    result = await session.execute(stmt)
    inserted_ids = list(result.scalars().all())

    logger.info(
        "code import done batch=%s sku=%s face=%s total=%d inserted=%d dup=%d",
        batch_id,
        sku_id,
        face_value,
        len(rows),
        len(inserted_ids),
        len(rows) - len(inserted_ids),
    )

    return ImportResult(
        batch_id=batch_id,
        total=len(rows),
        inserted=len(inserted_ids),
        skipped_duplicate=len(rows) - len(inserted_ids),
    )


async def lock_code(
    session: AsyncSession,
    *,
    sku_id: str,
    face_value: int,
    order_line_id: str,
) -> Code | None:
    """原子取一张 free 码并把它标成 locked，绑定到 order_line。

    用 FOR UPDATE SKIP LOCKED 允许多个 worker 并发取码而不会冲突。
    如果没货（全部被锁或被用完），返回 None。
    """
    pick_stmt = (
        select(Code.id)
        .where(
            Code.status == "free",
            Code.sku_id == sku_id,
            Code.face_value == face_value,
        )
        .order_by(Code.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    code_id = (await session.execute(pick_stmt)).scalar_one_or_none()
    if code_id is None:
        return None

    now = datetime.now(tz=timezone.utc)
    upd = (
        update(Code)
        .where(Code.id == code_id)
        .values(status="locked", order_line_id=order_line_id, locked_at=now)
        .returning(Code)
    )
    locked = (await session.execute(upd)).scalar_one()
    return locked


async def mark_used(session: AsyncSession, code_id: int) -> None:
    now = datetime.now(tz=timezone.utc)
    await session.execute(
        update(Code)
        .where(Code.id == code_id, Code.status == "locked")
        .values(status="used", used_at=now)
    )


async def release(session: AsyncSession, code_id: int) -> None:
    """把 locked 的码退回 free。CANCEL / 取消锁定僵尸检测用。"""
    await session.execute(
        update(Code)
        .where(Code.id == code_id, Code.status == "locked")
        .values(status="free", order_line_id=None, locked_at=None)
    )


async def void(session: AsyncSession, code_id: int) -> None:
    """已用码作废（比如 RETURN 时允许作废的策略）。"""
    await session.execute(update(Code).where(Code.id == code_id).values(status="void"))


async def count_free(
    session: AsyncSession, *, sku_id: str, face_value: int
) -> int:
    stmt = select(func.count(Code.id)).where(
        Code.status == "free",
        Code.sku_id == sku_id,
        Code.face_value == face_value,
    )
    return int((await session.execute(stmt)).scalar_one())


async def import_locked_code(
    session: AsyncSession,
    *,
    sku_id: str,
    face_value: int,
    plaintext: str,
    order_line_id: str,
    operator: str,
    batch_id: str | None = None,
    purchase_order_id: int | None = None,
) -> Code:
    """导入单张卡并直接锁定到订单行（API 实时采购用）。"""
    plain = plaintext.strip()
    if not plain:
        raise ValueError("empty code")

    now = datetime.now(tz=timezone.utc)
    code = Code(
        batch_id=batch_id or str(uuid.uuid4()),
        sku_id=sku_id,
        face_value=face_value,
        code_encrypted=encrypt(plain),
        code_hash=_hash_code(plain),
        status="locked",
        order_line_id=order_line_id,
        locked_at=now,
        operator=operator,
        purchase_order_id=purchase_order_id,
    )
    session.add(code)
    await session.flush()
    return code
