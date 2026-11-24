"""TikTok Shop Open API 请求签名模块。

参考来源：
- TT 官方文档 "Sign your API request"（登录可见，SPA）
- 开源社区已验证的实现（tudinhacoustic/tiktok-shop, stackoverflow 多篇）

算法：
1. 取所有 query 参数，剔除 `sign` 和 `access_token`
2. 按 key 字母序升序排序
3. 拼接成 `key1value1key2value2...`（无分隔符）
4. 前面拼 API path：`<path>key1value1key2value2...`
5. 若请求有 body（非 GET、非 multipart/form-data），末尾拼 body 的 raw string
6. 两端包 app_secret：`<app_secret><step5 结果><app_secret>`
7. 以 app_secret 为 key 做 HMAC-SHA256，输出 hex（小写）

边界：
- value 是数字 / bool 时，直接转字符串（Python 的 str() 即可，bool 会变 "True"/"False"，
  但 TT 文档没提 bool 参数，所以暂时按原始字符串处理；调用方应在传参前转成目标字符串）
- value 是 list / dict 时，TT Open API 不会出现这种情况（参数都是扁平结构），
  本模块保持严格 TypeError 以便尽早暴露问题
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Mapping


EXCLUDED_PARAMS = frozenset({"sign", "access_token"})


def _stringify(value: object) -> str:
    if isinstance(value, bool):
        # TT 文档没有 bool 参数，显式报错避免误用
        raise TypeError("Boolean query params are not supported by TT sign algorithm")
    if isinstance(value, (list, tuple, dict, set)):
        raise TypeError(
            f"Complex query param type {type(value).__name__!r} cannot be signed"
        )
    return str(value)


def build_sign_string(
    path: str,
    params: Mapping[str, object],
    app_secret: str,
    body: str | bytes | None = None,
) -> str:
    """构造用于签名的原始字符串，便于排错和单测观察中间结果。"""

    filtered = {k: _stringify(v) for k, v in params.items() if k not in EXCLUDED_PARAMS}
    sorted_pairs = sorted(filtered.items(), key=lambda kv: kv[0])

    base = "".join(f"{k}{v}" for k, v in sorted_pairs)
    base = f"{path}{base}"

    if body is not None and body != "" and body != b"":
        if isinstance(body, bytes):
            body = body.decode("utf-8")
        base = f"{base}{body}"

    return f"{app_secret}{base}{app_secret}"


def sign(
    path: str,
    params: Mapping[str, object],
    app_secret: str,
    body: str | bytes | None = None,
) -> str:
    """返回 TT API 要求的签名（hex lowercase）。"""

    sign_string = build_sign_string(path, params, app_secret, body)
    digest = hmac.new(
        app_secret.encode("utf-8"),
        sign_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest


def current_timestamp() -> int:
    """TT 文档要求 timestamp 是秒级 Unix 时间戳。"""

    return int(time.time())


def attach_signature(
    path: str,
    params: dict[str, object],
    app_secret: str,
    body: str | bytes | None = None,
) -> dict[str, object]:
    """就地给 params 追加 timestamp 和 sign，返回补全后的 dict。

    调用方拿到结果后直接作为 query string 发请求即可。
    """

    if "timestamp" not in params:
        params["timestamp"] = current_timestamp()
    params["sign"] = sign(path, params, app_secret, body)
    return params
