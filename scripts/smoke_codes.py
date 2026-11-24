"""卡密库 smoke test：
- 导入 5 张码
- 3 个 worker 并发取码（FOR UPDATE SKIP LOCKED）—— 验证不会抢同一张
- 核对水位

跑完会把这次的测试数据删掉，不污染 DB。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete  # noqa: E402

from app.auth.crypto import decrypt  # noqa: E402
from app.db.session import session_factory  # noqa: E402
from app.models.code import Code  # noqa: E402
from app.services.code_inventory import (  # noqa: E402
    count_free,
    import_codes,
    lock_code,
)


SKU = "__SMOKE_SKU__"
FACE = 1000


async def worker(sf, worker_id: int, order_line: str) -> int | None:
    async with sf() as session:
        async with session.begin():
            code = await lock_code(
                session, sku_id=SKU, face_value=FACE, order_line_id=order_line
            )
    if code is None:
        print(f"  worker-{worker_id} 没抢到码")
        return None
    plain = decrypt(code.code_encrypted)
    print(f"  worker-{worker_id} 抢到 id={code.id} plain={plain!r} line={order_line}")
    return code.id


async def main() -> None:
    sf = session_factory()

    async with sf() as s:
        async with s.begin():
            await s.execute(delete(Code).where(Code.sku_id == SKU))

    print("=" * 60)
    print(f"Step 1: 导入 5 张测试码到 sku={SKU} face={FACE}")
    print("=" * 60)
    async with sf() as session:
        async with session.begin():
            result = await import_codes(
                session,
                sku_id=SKU,
                face_value=FACE,
                plaintexts=[f"SMOKE-CODE-{i:03d}" for i in range(5)],
                operator="smoke",
            )
    print(f"  inserted={result.inserted} dup={result.skipped_duplicate}")
    assert result.inserted == 5

    print()
    print("=" * 60)
    print("Step 2: 第二次导入同样 5 张（应该全被去重）")
    print("=" * 60)
    async with sf() as session:
        async with session.begin():
            result2 = await import_codes(
                session,
                sku_id=SKU,
                face_value=FACE,
                plaintexts=[f"SMOKE-CODE-{i:03d}" for i in range(5)],
                operator="smoke",
            )
    print(f"  inserted={result2.inserted} dup={result2.skipped_duplicate}")
    assert result2.inserted == 0 and result2.skipped_duplicate == 5

    async with sf() as session:
        free = await count_free(session, sku_id=SKU, face_value=FACE)
    print(f"  水位 free={free}")
    assert free == 5

    print()
    print("=" * 60)
    print("Step 3: 3 个 worker 并发抢码（每个拿 1 张）")
    print("=" * 60)
    ids = await asyncio.gather(
        worker(sf, 1, "order-line-A"),
        worker(sf, 2, "order-line-B"),
        worker(sf, 3, "order-line-C"),
    )
    unique = set(i for i in ids if i is not None)
    assert len(unique) == 3, f"应该拿到 3 张不同的码，但拿到 {unique}"
    print(f"  ✅ 3 张码都不同：{sorted(unique)}")

    async with sf() as session:
        free = await count_free(session, sku_id=SKU, face_value=FACE)
    print(f"  抢完后水位 free={free}（应为 2）")
    assert free == 2

    print()
    print("=" * 60)
    print("Step 4: 5 个 worker 再并发（库存只剩 2，3 个会拿不到）")
    print("=" * 60)
    ids2 = await asyncio.gather(
        *[worker(sf, 10 + i, f"order-line-{chr(ord('D')+i)}") for i in range(5)]
    )
    got = [i for i in ids2 if i is not None]
    missed = [i for i in ids2 if i is None]
    print(f"  抢到 {len(got)} 张，{len(missed)} 个 worker 没抢到")
    assert len(got) == 2

    async with sf() as session:
        free = await count_free(session, sku_id=SKU, face_value=FACE)
    assert free == 0
    print(f"  水位 free=0 ✅")

    async with sf() as s:
        async with s.begin():
            deleted = await s.execute(delete(Code).where(Code.sku_id == SKU))
    print()
    print(f"清理测试数据：删了 {deleted.rowcount} 行")

    print()
    print("=" * 60)
    print("全部通过 ✅")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
