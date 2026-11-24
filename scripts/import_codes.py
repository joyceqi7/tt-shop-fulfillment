"""批量导入卡密 CLI。

用法：
    python scripts/import_codes.py \\
        --file codes.txt \\
        --sku-id AMZ_10USD \\
        --face-value 1000 \\
        --operator bruce

文件格式：一行一张明文卡密，空行和前后空格会被忽略。

特性：
- Fernet 加密后存库，DB 泄了也拿不到明文
- SHA-256 去重：同一张码在同一 batch 或跨 batch 重复都会被跳过
- 打印导入结果（total / inserted / skipped_duplicate）
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import session_factory  # noqa: E402
from app.services.code_inventory import import_codes  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Import plaintext codes into codes table")
    p.add_argument("--file", "-f", required=True, type=Path, help="明文卡密文件")
    p.add_argument("--sku-id", required=True, help="内部 SKU ID")
    p.add_argument(
        "--face-value",
        required=True,
        type=int,
        help="面额（美分，例 1000 表示 $10.00）",
    )
    p.add_argument("--operator", required=True, help="导入人（审计）")
    return p.parse_args()


async def main() -> int:
    args = parse_args()
    if not args.file.exists():
        print(f"❌ 文件不存在：{args.file}", file=sys.stderr)
        return 2

    plaintexts = [line for line in args.file.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not plaintexts:
        print("❌ 文件为空", file=sys.stderr)
        return 2

    print(f"准备导入 {len(plaintexts)} 条到 sku_id={args.sku_id} face_value={args.face_value}")
    sf = session_factory()
    async with sf() as session:
        async with session.begin():
            result = await import_codes(
                session,
                sku_id=args.sku_id,
                face_value=args.face_value,
                plaintexts=plaintexts,
                operator=args.operator,
            )

    print("=" * 50)
    print(f"batch_id:          {result.batch_id}")
    print(f"total:             {result.total}")
    print(f"inserted:          {result.inserted}")
    print(f"skipped_duplicate: {result.skipped_duplicate}")
    print("=" * 50)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
