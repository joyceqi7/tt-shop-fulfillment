"""生成 admin 后台登录密码的 bcrypt 哈希。

用法：
    python scripts/gen_admin_hash.py
    # 交互式输入两次密码 → 打印 bcrypt 哈希到 stdout

把输出贴进 .env.production 的 ADMIN_PASSWORD_HASH 字段，**不要** push 到 git。

为什么交互式而不是 argv：argv 会留在 shell history 和 ps 里，密码就泄了。
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

# 让脚本不论从哪个 cwd 调用都能找到 app.* 模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.admin_session import hash_password  # noqa: E402


def main() -> int:
    p1 = getpass.getpass("admin password: ")
    if len(p1) < 12:
        print("ERROR: password too short (>= 12 chars 推荐)", file=sys.stderr)
        return 1
    p2 = getpass.getpass("confirm        : ")
    if p1 != p2:
        print("ERROR: passwords do not match", file=sys.stderr)
        return 1

    h = hash_password(p1)
    print()
    print("=== bcrypt hash (复制下面这行到 .env.production)===")
    print(f"ADMIN_PASSWORD_HASH={h}")
    print()
    print("还需要生成一个 32 字节的 SESSION_SECRET，例如：")
    print("  python -c 'import secrets; print(secrets.token_urlsafe(32))'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
