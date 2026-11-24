"""TT Shop webhook 验签。

⚠️ TT Onboarding 文档只描述了 webhook 的订阅机制和事件类型，
   没有给出明确的 HTTP 签名算法和 header 名。这里实现两步占位：
   1) 默认按业界惯例：HMAC-SHA256(app_secret, raw_body) → hex lowercase
   2) 提供 settings.webhook_verify_disabled 开关用于联调期跳过

联调拿到第一个真实 webhook payload 时，需要确认：
- header 名（例：`tts-signature` / `Authorization` / `X-Tts-Sign`）
- 签名算法（HMAC-SHA256 of body / 还是包含 timestamp 防重放）
- 编码（hex / base64）

确认后**只需要修这一个文件**，其他都不动。
"""

from __future__ import annotations

import hashlib
import hmac


def verify(body: bytes, signature: str | None, app_secret: str) -> bool:
    """返回 True 表示验签通过。"""
    if not signature:
        return False
    expected = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.lower(), signature.strip().lower())


def sign(body: bytes, app_secret: str) -> str:
    """生成签名（用于本地 smoke test 模拟 TT 推送）。"""
    return hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
