"""WGCards API 客户端。

支持 AES-ECB-PKCS7 加解密、Token 管理、统一请求封装。
文档：https://api.wgcards.com
"""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

logger = logging.getLogger(__name__)


# ============================================================================
# AES 加解密工具
# ============================================================================


class AesUtil:
    """AES-ECB-PKCS7 加解密工具，匹配 WGCards 文档规范。"""

    @staticmethod
    def _is_hex(value: str) -> bool:
        s = value.strip()
        if len(s) < 32 or len(s) % 2 != 0:
            return False
        try:
            int(s, 16)
        except ValueError:
            return False
        return True

    @staticmethod
    def decode_ciphertext(encoded: str) -> bytes:
        """密文可能是 hex 或 base64（测试环境常见 base64）。"""
        s = encoded.strip()
        if AesUtil._is_hex(s):
            return bytes.fromhex(s)
        pad = "=" * ((4 - len(s) % 4) % 4)
        try:
            return base64.b64decode(s + pad, validate=False)
        except Exception as e:
            raise ValueError(f"unsupported ciphertext encoding: {e}") from e

    @staticmethod
    def _pad(data: bytes) -> bytes:
        """PKCS7 填充。"""
        padder = PKCS7(128).padder()
        return padder.update(data) + padder.finalize()

    @staticmethod
    def _unpad(data: bytes) -> bytes:
        """PKCS7 填充移除。"""
        unpadder = PKCS7(128).unpadder()
        return unpadder.update(data) + unpadder.finalize()

    @staticmethod
    def encrypt(key: str, content: str) -> str:
        """AES-ECB 加密，返回 base64（与 WGCards/hutool encryptBase64 一致）。"""
        key_bytes = key.encode("utf-8")
        cipher = Cipher(algorithms.AES(key_bytes), modes.ECB())
        encryptor = cipher.encryptor()
        padded = AesUtil._pad(content.encode("utf-8"))
        encrypted = encryptor.update(padded) + encryptor.finalize()
        return base64.b64encode(encrypted).decode("ascii")

    @staticmethod
    def decrypt(key: str, encrypted: str) -> str:
        """AES-ECB 解密，密文支持 hex 或 base64。"""
        key_bytes = key.encode("utf-8")
        encrypted_bytes = AesUtil.decode_ciphertext(encrypted)
        cipher = Cipher(algorithms.AES(key_bytes), modes.ECB())
        decryptor = cipher.decryptor()
        decrypted = decryptor.update(encrypted_bytes) + decryptor.finalize()
        return AesUtil._unpad(decrypted).decode("utf-8")


# ============================================================================
# 数据模型
# ============================================================================


@dataclass
class WGCardsAccount:
    """账户信息。"""
    wallet_id: str
    currency: str
    balance: float
    effective: bool


@dataclass
class WGCardsSku:
    """SKU 规格信息。"""
    sku_id: str
    sku_name: str
    sku_price: float | None
    sku_price_currency: str
    max_face_value: float
    min_face_value: float
    max_price: float
    min_price: float
    stock: int | None = None


@dataclass
class WGCardsItem:
    """商品信息。"""
    item_id: str
    item_name: str
    item_title: str
    item_brand_name: str
    currency_code: str
    description: str
    how_exchange: str
    spu_type: int
    spu_image: str | None
    skus: list[WGCardsSku] = field(default_factory=list)


@dataclass
class WGCardsStock:
    """库存信息。"""
    item_id: str
    sku_id: str
    number: int  # -1 表示无限制


@dataclass
class WGCardsOrderDetail:
    """订单详情。"""
    order_id: str
    request_id: str
    order_status: int  # 0:未完成, 1:已完成
    delivery_status: int  # 1:待发货, 2:部分发货, 3:全部发货完成
    order_way: int
    create_time: str
    total_amount: float
    cur: str
    order_direct_status: int | None = None


@dataclass
class WGCardsOrderLine:
    """订单明细项。"""
    sku_id: str
    sku_name: str
    spu_name: str
    is_end: int  # 0:未完成, 1:已完成
    buy_num: int
    face_value: float
    send_out_num: int
    original_price: float
    original_price_cur: str
    actual_paid_price: float
    actual_paid_price_cur: str
    discount_ratio_str: str


@dataclass
class WGCardsCard:
    """卡密信息。"""
    sku_id: str
    card: str
    pin_code: str
    sn_code: str


@dataclass
class WGCardsPlaceOrderResult:
    """下单结果。"""
    success: bool
    order_id: str | None
    message: str


class WGCardsAPIError(Exception):
    """WGCards API 调用异常。"""
    pass


class WGCardsRateLimitError(WGCardsAPIError):
    """WGCards API 频率限制。"""

    def __init__(
        self,
        endpoint: str,
        message: str,
        retry_after_seconds: int | None = None,
    ):
        self.endpoint = endpoint
        self.message = message
        self.retry_after_seconds = retry_after_seconds
        retry_hint = (
            f"，请 {retry_after_seconds} 秒后重试"
            if retry_after_seconds is not None
            else "，请稍后重试"
        )
        super().__init__(f"供应商限流{retry_hint}（{message}）")


class WGCardsTimeoutError(Exception):
    """WGCards 操作超时。"""
    pass


# vendor code 提示（msg 为空时作补充）
WGCARDS_ORDER_HINTS: dict[str, dict[int, str]] = {
    "placeOrder": {
        -101: "礼品卡：faceValue 与 SKU 不符、非礼品卡 SKU、serviceOrder 重复",
    },
}

# WGCards spuType 映射（与供应商文档一致）
WGCARDS_SPU_TYPES: dict[int, str] = {
    1: "游戏",
    2: "礼品卡",
    3: "软件",
    4: "微软商品",
    5: "充值商品",
    7: "WWG 推荐",
    8: "充值兑换码",
    9: "Esim 商品",
    11: "任天堂游戏",
}


class _TTLCache:
    """进程内短 TTL 缓存，减少 WGCards 后台页面重复点击造成的限流。"""

    def __init__(self, max_items: int = 2048):
        self.max_items = max_items
        self._items: dict[tuple[Any, ...], tuple[float, Any]] = {}

    def get(self, key: tuple[Any, ...]) -> Any | None:
        item = self._items.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at <= time.time():
            self._items.pop(key, None)
            return None
        return copy.deepcopy(value)

    def set(self, key: tuple[Any, ...], value: Any, ttl_seconds: int) -> None:
        now = time.time()
        if len(self._items) >= self.max_items:
            for cached_key, (expires_at, _) in list(self._items.items()):
                if expires_at <= now:
                    self._items.pop(cached_key, None)
            if len(self._items) >= self.max_items:
                self._items.pop(next(iter(self._items)), None)
        self._items[key] = (now + ttl_seconds, copy.deepcopy(value))

    def delete(self, key: tuple[Any, ...]) -> None:
        self._items.pop(key, None)

    def clear(self) -> None:
        self._items.clear()


_WGCARDS_CACHE = _TTLCache()


def _retry_after_seconds(message: str) -> int | None:
    match = re.search(r"retry\s+after\s+(\d+)\s*seconds?", message, re.I)
    if match:
        return int(match.group(1))
    match = re.search(r"(\d+)\s*秒", message)
    if match:
        return int(match.group(1))
    return None


def _is_rate_limit_message(message: str, code: int | None = None) -> bool:
    text = message.lower()
    return (
        ("frequency" in text and "limit" in text)
        or "exceeds limit" in text
        or "rate limit" in text
        or "频率" in message
        or code == 429
    )


# ============================================================================
# WGCards API 客户端
# ============================================================================


class WGCardsClient:
    """WGCards API 客户端。

    使用方式：
    ```python
    client = WGCardsClient(
        host="https://api.example.com",
        app_id="2025112058411324",
        account_id="2025112058411325",
        app_key="o%Cmiq52TP4o06Uok&R6tC#^#FXGNE*3",
    )
    token = await client.get_token()
    account = await client.get_account()
    ```
    """

    def __init__(
        self,
        host: str,
        app_id: str,
        account_id: str,
        app_key: str,
        timeout: int = 30,
    ):
        self.host = host.rstrip("/")
        self.app_id = app_id
        self.account_id = account_id
        self.app_key = app_key
        self.timeout = timeout

        self._token: str | None = None
        self._token_expires_at: float = 0

    def _cache_key(self, scope: str, *parts: Any) -> tuple[Any, ...]:
        key_fingerprint = hashlib.sha256(self.app_key.encode("utf-8")).hexdigest()[:12]
        return (
            "wgcards",
            scope,
            self.host,
            self.app_id,
            self.account_id,
            key_fingerprint,
            *parts,
        )

    @staticmethod
    def _unwrap_json(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if not stripped or stripped[0] not in "{[":
            return value
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return value

    def _as_dict(self, value: Any, *, context: str) -> dict:
        value = self._unwrap_json(value)
        if isinstance(value, dict):
            return value
        raise WGCardsAPIError(
            f"{context}: expected object, got {type(value).__name__}"
        )

    def _extract_token(self, resp: Any) -> str:
        resp = self._unwrap_json(resp)
        if isinstance(resp, str) and resp.strip():
            return resp.strip()
        if isinstance(resp, dict):
            code = resp.get("code")
            if code is not None and int(code) != 200:
                msg = self._pick_message(resp) or f"getToken failed (code={code})"
                raise WGCardsAPIError(msg)

            data = self._unwrap_json(resp.get("data"))
            if isinstance(data, str) and data.strip():
                return data.strip()
            if isinstance(data, dict):
                inner_code = data.get("code")
                if inner_code is not None and int(inner_code) != 200:
                    msg = (
                        self._pick_message(data, resp)
                        or f"getToken failed (code={inner_code})"
                    )
                    raise WGCardsAPIError(msg)
                for key in ("token", "accessToken", "access_token", "accessTokenValue"):
                    val = data.get(key)
                    if isinstance(val, str) and val.strip():
                        return val.strip()
            for key in ("token", "accessToken", "access_token", "accessTokenValue"):
                val = resp.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()

            msg = self._pick_message(resp)
            if msg:
                raise WGCardsAPIError(f"getToken: {msg}")

            preview = json.dumps(resp, ensure_ascii=False)[:240]
            logger.warning("getToken unexpected response shape: %s", preview)
            raise WGCardsAPIError(
                f"getToken: cannot parse token from response ({preview})"
            )
        raise WGCardsAPIError("getToken: cannot parse token from response")

    def _extract_accounts(self, resp: Any) -> list[dict]:
        root = self._as_dict(resp, context="getAccount")
        data = self._unwrap_json(root.get("data", root))
        accounts: Any = None
        if isinstance(data, dict):
            accounts = data.get("accounts") or data.get("accountList")
        elif isinstance(data, list):
            accounts = data
        if accounts is None:
            accounts = root.get("accounts") or root.get("accountList")
        accounts = self._unwrap_json(accounts)
        if isinstance(accounts, dict):
            accounts = [accounts]
        if not isinstance(accounts, list) or not accounts:
            raise WGCardsAPIError("getAccount: no account returned")
        parsed: list[dict] = []
        for item in accounts:
            item = self._unwrap_json(item)
            if isinstance(item, dict):
                parsed.append(item)
        if not parsed:
            raise WGCardsAPIError("getAccount: invalid account entries")
        return parsed

    def _extract_page(self, resp: Any, *, context: str) -> dict[str, Any]:
        """解析 getItem / getOrderInfo 等分页响应。"""
        root = self._as_dict(resp, context=context)
        payload: Any = self._unwrap_json(root.get("data", root))

        if isinstance(payload, str):
            payload = self._unwrap_json(payload)
        if isinstance(payload, list):
            return {"current": 1, "pages": 1, "records": payload}
        if not isinstance(payload, dict):
            raise WGCardsAPIError(
                f"{context}: expected page object, got {type(payload).__name__}"
            )

        inner = self._unwrap_json(payload.get("data"))
        if isinstance(inner, dict) and "records" in inner:
            payload = inner

        records = payload.get("records")
        if records is None:
            records = []
        if not isinstance(records, list):
            raise WGCardsAPIError(f"{context}: records must be a list")

        normalized: list[dict[str, Any]] = []
        for item in records:
            item = self._unwrap_json(item)
            if isinstance(item, dict):
                normalized.append(self._normalize_item(item))

        return {
            "current": int(payload.get("current") or 1),
            "pages": int(payload.get("pages") or 1),
            "records": normalized,
        }

    @staticmethod
    def _pick_message(*sources: Any) -> str:
        """从多种 WGCards 响应字段中提取可读错误信息。"""
        keys = (
            "message",
            "msg",
            "errorMsg",
            "errMsg",
            "error",
            "reason",
            "detail",
            "description",
        )
        for src in sources:
            if not isinstance(src, dict):
                continue
            for key in keys:
                val = src.get(key)
                if val is not None and str(val).strip():
                    return str(val).strip()
            for key, val in src.items():
                if (
                    isinstance(key, str)
                    and key.lower() in {"message", "msg", "errormsg", "errmsg"}
                    and val is not None
                    and str(val).strip()
                ):
                    return str(val).strip()
        return ""

    def _normalize_item(self, item: dict[str, Any]) -> dict[str, Any]:
        skus = (
            item.get("skus")
            or item.get("skuInfos")
            or item.get("skuList")
            or item.get("sku_list")
            or []
        )
        if not isinstance(skus, list):
            skus = []
        normalized_skus: list[dict[str, Any]] = []
        for sku in skus:
            sku = self._unwrap_json(sku)
            if isinstance(sku, dict):
                normalized_skus.append(sku)
        return {
            **item,
            "skus": normalized_skus,
        }

    def _match_sku_from_records(
        self,
        records: list[Any],
        target: str,
        *,
        include_item_id: bool,
    ) -> dict[str, Any] | None:
        for raw_item in records:
            raw_item = self._unwrap_json(raw_item)
            if not isinstance(raw_item, dict):
                continue
            item = self._normalize_item(raw_item)
            for raw_sku in item.get("skus") or []:
                sku = self._unwrap_json(raw_sku)
                if not isinstance(sku, dict):
                    continue
                if str(sku.get("skuId", "")).strip() != target:
                    continue
                if include_item_id:
                    return {
                        **item,
                        "skus": [sku],
                    }
                return {
                    "skuId": target,
                    "skuName": sku.get("skuName"),
                    "itemName": item.get("itemName"),
                    "spuType": item.get("spuType"),
                    "minFaceValue": sku.get("minFaceValue"),
                    "maxFaceValue": sku.get("maxFaceValue"),
                    "skuPrice": sku.get("skuPrice"),
                    "skuPriceCurrency": sku.get("skuPriceCurrency"),
                    "stock": sku.get("stock"),
                }
        return None

    def _extract_place_order_result(
        self,
        resp: Any,
        *,
        operation: str = "placeOrder",
    ) -> WGCardsPlaceOrderResult:
        """解析 placeOrder 响应（结构因环境而异）。"""
        root = self._as_dict(resp, context=operation)
        payload: Any = self._unwrap_json(root.get("data", root))
        op_label = "place_order"
        hints = WGCARDS_ORDER_HINTS.get(operation, {})

        if isinstance(payload, str) and payload.strip():
            return WGCardsPlaceOrderResult(
                success=True,
                order_id=payload.strip(),
                message=self._pick_message(root),
            )

        if isinstance(payload, (int, float)) and not isinstance(payload, bool):
            return WGCardsPlaceOrderResult(
                success=True,
                order_id=str(int(payload)),
                message="",
            )

        if not isinstance(payload, dict):
            raise WGCardsAPIError(
                f"{operation}: unexpected data type {type(payload).__name__}"
            )

        code = payload.get("code", root.get("code"))
        if code is not None and int(code) != 200:
            msg = self._pick_message(payload, root)
            if not msg:
                hint = hints.get(int(code), "")
                detail = json.dumps(payload, ensure_ascii=False)[:240]
                msg = f"{op_label} failed (code={code})"
                if hint:
                    msg = f"{msg}: {hint}"
                if detail and detail != "{}":
                    msg = f"{msg} | {detail}"
            logger.warning(
                "WGCards %s rejected code=%s payload=%s",
                operation,
                code,
                json.dumps(payload, ensure_ascii=False)[:500],
            )
            return WGCardsPlaceOrderResult(success=False, order_id=None, message=msg)

        if payload.get("success") is False:
            msg = self._pick_message(payload, root) or f"{op_label} failed"
            logger.warning(
                "WGCards %s success=false payload=%s",
                operation,
                json.dumps(payload, ensure_ascii=False)[:500],
            )
            return WGCardsPlaceOrderResult(success=False, order_id=None, message=msg)

        order_id: Any = (
            payload.get("data")
            or payload.get("orderId")
            or payload.get("order_id")
        )
        if isinstance(order_id, dict):
            inner_code = order_id.get("code")
            if inner_code is not None and int(inner_code) != 200:
                msg = self._pick_message(order_id, payload, root)
                if not msg:
                    hint = hints.get(int(inner_code), "")
                    msg = f"{op_label} failed (code={inner_code})"
                    if hint:
                        msg = f"{msg}: {hint}"
                return WGCardsPlaceOrderResult(
                    success=False, order_id=None, message=msg
                )
            order_id = order_id.get("orderId") or order_id.get("id")
        if order_id is None:
            order_id = root.get("orderId") or root.get("order_id")

        if order_id is not None and str(order_id).strip():
            return WGCardsPlaceOrderResult(
                success=True,
                order_id=str(order_id).strip(),
                message=self._pick_message(payload, root),
            )

        msg = self._pick_message(payload, root)
        if msg:
            logger.warning(
                "WGCards %s no order id, message=%s payload=%s",
                operation,
                msg,
                json.dumps(payload, ensure_ascii=False)[:500],
            )
            return WGCardsPlaceOrderResult(success=False, order_id=None, message=msg)

        logger.warning(
            "WGCards %s no order id payload=%s",
            operation,
            json.dumps({"root": root, "payload": payload}, ensure_ascii=False)[:800],
        )
        raise WGCardsAPIError(f"{operation}: no order id in response")

    def _extract_card_records(self, resp: Any, *, context: str) -> list[dict[str, Any]]:
        root = self._as_dict(resp, context=context)
        data: Any = self._unwrap_json(root.get("data", root))
        if isinstance(data, dict):
            records = data.get("records")
            inner = self._unwrap_json(data.get("data"))
            if records is None and isinstance(inner, dict):
                records = inner.get("records")
            if records is None and isinstance(inner, list):
                records = inner
        elif isinstance(data, list):
            records = data
        else:
            records = []
        if not isinstance(records, list):
            raise WGCardsAPIError(f"{context}: records must be a list")
        parsed: list[dict[str, Any]] = []
        for rec in records:
            rec = self._unwrap_json(rec)
            if isinstance(rec, dict):
                parsed.append(rec)
        return parsed

    def _looks_like_ciphertext(self, value: str) -> bool:
        """WGCards 密文为 AES 加密后的 hex 或 base64 字符串。"""
        s = value.strip()
        if len(s) < 16:
            return False
        if AesUtil._is_hex(s):
            return True
        # base64 字符集（含 + / =）
        allowed = set(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
        )
        if not set(s) <= allowed:
            return False
        try:
            AesUtil.decode_ciphertext(s)
            return True
        except ValueError:
            return False

    def _decode_encrypted_msg(self, encrypted: str, endpoint: str) -> dict:
        decrypted = self._decrypt_msg(encrypted)
        decrypted = self._unwrap_json(decrypted)
        if isinstance(decrypted, str):
            if endpoint.endswith("getToken"):
                return {"data": decrypted}
            inner = self._unwrap_json(decrypted)
            if isinstance(inner, dict):
                return inner
            raise WGCardsAPIError(
                f"Unexpected decrypted string for {endpoint}"
            )
        if isinstance(decrypted, dict):
            return decrypted
        raise WGCardsAPIError(
            f"Unexpected decrypted type for {endpoint}: {type(decrypted).__name__}"
        )

    def _parse_envelope(self, envelope: dict, endpoint: str) -> dict:
        code = envelope.get("code")
        if code is not None and int(code) != 200:
            msg = envelope.get("msg") or envelope.get("message") or ""
            code_int = int(code)
            if _is_rate_limit_message(str(msg), code_int):
                raise WGCardsRateLimitError(
                    endpoint,
                    str(msg),
                    retry_after_seconds=_retry_after_seconds(str(msg)),
                )
            raise WGCardsAPIError(
                f"API error on {endpoint}: code={code}, msg={msg}"
            )
        msg = envelope.get("msg")
        if msg:
            return self._decode_encrypted_msg(str(msg), endpoint)
        return envelope

    def _parse_http_response(self, resp: httpx.Response, endpoint: str) -> dict:
        """兼容 WGCards 多种响应：{code,msg} 信封 / 顶层密文字符串 / 明文 JSON。"""
        raw_text = (resp.text or "").strip()
        try:
            parsed: Any = resp.json()
        except json.JSONDecodeError:
            parsed = raw_text

        parsed = self._unwrap_json(parsed)

        if isinstance(parsed, str):
            if self._looks_like_ciphertext(parsed):
                return self._decode_encrypted_msg(parsed, endpoint)
            inner = self._unwrap_json(parsed)
            if isinstance(inner, dict):
                return self._parse_envelope(inner, endpoint)
            if isinstance(inner, str) and self._looks_like_ciphertext(inner):
                return self._decode_encrypted_msg(inner, endpoint)
            preview = parsed[:120] + ("…" if len(parsed) > 120 else "")
            raise WGCardsAPIError(f"Unexpected string response: {preview}")

        if isinstance(parsed, dict):
            return self._parse_envelope(parsed, endpoint)

        raise WGCardsAPIError(
            f"Unexpected top-level response: {type(parsed).__name__}"
        )

    # --------------------------------------------------------------------------
    # Token 管理
    # --------------------------------------------------------------------------

    async def get_token(self) -> str:
        """获取 access token，跨请求缓存约 115 分钟。"""
        now = time.time()
        if self._token and now < self._token_expires_at - 300:
            return self._token

        cached = _WGCARDS_CACHE.get(self._cache_key("token"))
        if isinstance(cached, str) and cached:
            self._token = cached
            self._token_expires_at = now + 600
            return cached

        body = {"appId": self.app_id, "appKey": self.app_key}
        resp = await self._request("/api/getToken", body, use_token=False)
        self._token = self._extract_token(resp)
        self._token_expires_at = now + 6900
        _WGCARDS_CACHE.set(self._cache_key("token"), self._token, ttl_seconds=6900)
        logger.info("WGCards token refreshed, expires at %s", self._token_expires_at)
        return self._token

    def clear_token(self):
        """清除缓存的 token，强制下次重新获取。"""
        self._token = None
        self._token_expires_at = 0
        _WGCARDS_CACHE.delete(self._cache_key("token"))

    # --------------------------------------------------------------------------
    # 加解密
    # --------------------------------------------------------------------------

    def _encrypt_msg(self, body: dict) -> str:
        """加密请求体 msg 字段。"""
        return AesUtil.encrypt(self.app_id, json.dumps(body, ensure_ascii=False))

    def _decrypt_msg(self, encrypted: str) -> Any:
        """解密响应体 msg 字段。"""
        try:
            plain = AesUtil.decrypt(self.app_id, encrypted)
        except Exception as e:
            raise WGCardsAPIError(f"decrypt failed: {e}") from e
        try:
            return json.loads(plain)
        except json.JSONDecodeError:
            return plain

    # --------------------------------------------------------------------------
    # 统一请求
    # --------------------------------------------------------------------------

    async def _request(
        self,
        endpoint: str,
        body: dict,
        use_token: bool = True,
        max_retries: int = 3,
    ) -> dict:
        """统一请求方法，自动处理加密/解密/重试。"""
        url = f"{self.host}{endpoint}"
        encrypted_msg = self._encrypt_msg(body)
        encrypted_body: dict[str, Any] = {
            "appId": self.app_id,
            "accountId": self.account_id,
            "msg": encrypted_msg,
        }

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if use_token:
            token = await self.get_token()
            # 文档：除 getToken 外必须在 Header 带 appId + Authorization
            headers["appId"] = self.app_id
            headers["Authorization"] = f"Bearer {token}"

        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(url, json=encrypted_body, headers=headers)
                    resp.raise_for_status()

                return self._parse_http_response(resp, endpoint)

            except httpx.HTTPStatusError as e:
                logger.warning("WGCards request failed (attempt %d): %s", attempt + 1, e)
                if attempt == max_retries - 1:
                    raise WGCardsAPIError(f"HTTP {e.response.status_code}: {e}") from e
                await self._retry_delay(attempt)

            except httpx.RequestError as e:
                logger.warning("WGCards network error (attempt %d): %s", attempt + 1, e)
                if attempt == max_retries - 1:
                    raise WGCardsAPIError(f"Network error: {e}") from e
                await self._retry_delay(attempt)

            except WGCardsRateLimitError as e:
                if attempt > 0 or attempt == max_retries - 1:
                    raise
                retry_after = min(max(e.retry_after_seconds or 5, 1), 30)
                logger.warning(
                    "WGCards rate limited %s, retrying after %ss: %s",
                    endpoint,
                    retry_after,
                    e.message,
                )
                await asyncio.sleep(retry_after)

            except WGCardsAPIError as e:
                transient = "code=500" in str(e) or "please try later" in str(e).lower()
                if not transient or attempt == max_retries - 1:
                    raise
                logger.warning(
                    "WGCards transient error (attempt %d) %s: %s",
                    attempt + 1,
                    endpoint,
                    e,
                )
                await self._retry_delay(attempt)

            except (json.JSONDecodeError, KeyError, ValueError, AttributeError, TypeError) as e:
                logger.warning("WGCards parse failed (attempt %d): %s", attempt + 1, e)
                if attempt == max_retries - 1:
                    raise WGCardsAPIError(f"Invalid API response: {e}") from e
                await self._retry_delay(attempt)

        raise WGCardsAPIError("Max retries exceeded")

    @staticmethod
    async def _retry_delay(attempt: int):
        """指数退避延迟。"""
        await asyncio.sleep(2 ** attempt * 0.5)

    # --------------------------------------------------------------------------
    # API 方法
    # --------------------------------------------------------------------------

    async def get_account(self) -> WGCardsAccount:
        """获取账户信息（余额）。"""
        cache_key = self._cache_key("account")
        cached = _WGCARDS_CACHE.get(cache_key)
        if isinstance(cached, WGCardsAccount):
            return cached

        body = {"userId": self.app_id}
        resp = await self._request("/api/getAccount", body)
        acc = self._extract_accounts(resp)[0]
        account = WGCardsAccount(
            wallet_id=str(acc.get("walletId", "")),
            currency=str(acc.get("currency", "")),
            balance=float(acc.get("balance", 0) or 0),
            effective=bool(acc.get("effective", False)),
        )
        _WGCARDS_CACHE.set(cache_key, account, ttl_seconds=60)
        return account

    async def get_item(
        self,
        currency_code: str = "CNY",
        language: str = "en",
        current: int = 1,
        size: int = 10,
        item_id: str = "",
        item_name: str = "",
    ) -> dict:
        """获取商品列表（分页）。"""
        cache_key = self._cache_key(
            "get_item",
            currency_code,
            language,
            current,
            size,
            item_id.strip(),
            item_name.strip(),
        )
        cached = _WGCARDS_CACHE.get(cache_key)
        if isinstance(cached, dict):
            return cached

        body = {
            "appId": self.app_id,
            "currencyCode": currency_code,
            "language": language,
            "current": current,
            "itemId": item_id,
            "itemName": item_name,
            "size": size,
        }
        resp = await self._request("/api/getItem", body)
        page = self._extract_page(resp, context="getItem")
        _WGCARDS_CACHE.set(cache_key, page, ttl_seconds=300)
        return page

    async def lookup_sku(
        self,
        sku_id: str,
        *,
        currency_code: str = "CNY",
        max_pages: int = 20,
        include_item_id: bool = False,
    ) -> dict[str, Any] | None:
        """在商品目录中查找 SKU（含 spuType、面值范围、采购价）。

        V3.0.6 文档说明 getItemAndStock 传入 skuId 时不受 40次/小时限制，
        因此优先精准查询，失败时才回退到分页目录扫描。
        """
        target = str(sku_id).strip()
        if not target:
            return None

        cache_key = self._cache_key(
            "lookup_sku",
            target,
            currency_code,
            include_item_id,
        )
        cached = _WGCARDS_CACHE.get(cache_key)
        if isinstance(cached, dict):
            return cached

        try:
            exact_page = await self.get_item_and_stock(
                currency_code=currency_code,
                current=1,
                size=10,
                sku_id=target,
            )
            matched = self._match_sku_from_records(
                exact_page.get("records") or [],
                target,
                include_item_id=include_item_id,
            )
            if matched:
                _WGCARDS_CACHE.set(cache_key, matched, ttl_seconds=300)
                return matched
        except WGCardsRateLimitError:
            raise
        except WGCardsAPIError as e:
            logger.info("WGCards exact sku lookup failed, falling back: %s", e)

        for page_num in range(1, max_pages + 1):
            page = await self.get_item(
                currency_code=currency_code,
                current=page_num,
                size=50,
            )
            matched = self._match_sku_from_records(
                page.get("records") or [],
                target,
                include_item_id=include_item_id,
            )
            if matched:
                _WGCARDS_CACHE.set(cache_key, matched, ttl_seconds=300)
                return matched
            if page_num >= int(page.get("pages") or 1):
                break
        return None

    @staticmethod
    def resolve_place_order_face_value(
        catalog: dict[str, Any],
        requested: float | None,
    ) -> float | None:
        """根据目录信息解析 placeOrder 所需 faceValue。"""
        spu = catalog.get("spuType")
        if spu is not None and int(spu) != 2:
            spu_label = WGCARDS_SPU_TYPES.get(int(spu), str(spu))
            raise ValueError(
                f"SKU {catalog.get('skuId')} 是 spuType={spu}（{spu_label}），"
                "不能走 placeOrder 礼品卡下单"
            )

        min_f = float(catalog.get("minFaceValue") or 0)
        max_f = float(catalog.get("maxFaceValue") or 0)

        if min_f == max_f and min_f > 0:
            if requested is not None and abs(float(requested) - min_f) > 0.01:
                raise ValueError(
                    f"固定面值 SKU 须 faceValue={min_f}，链接里填的是 {requested}"
                )
            return min_f

        if requested is None:
            raise ValueError(
                f"可变面值 SKU {catalog.get('skuId')} 须在 API 链接中填写外部面值"
                f"（范围 {min_f}–{max_f}）"
            )
        req = float(requested)
        if req < min_f or req > max_f:
            raise ValueError(
                f"faceValue={req} 超出 SKU 允许范围 {min_f}–{max_f}"
            )
        return req

    async def get_all_items(
        self,
        currency_code: str = "CNY",
        language: str = "en",
    ) -> list[dict]:
        """获取全部商品（不分页，限制较低；缓存 1 小时）。"""
        cache_key = self._cache_key("get_all_items", currency_code, language)
        cached = _WGCARDS_CACHE.get(cache_key)
        if isinstance(cached, list):
            return cached

        body = {
            "appId": self.app_id,
            "currencyCode": currency_code,
            "language": language,
            "itemId": "",
            "itemName": "",
        }
        resp = await self._request("/api/getAllItem", body)
        data = resp["data"]
        items = [
            self._normalize_item(item)
            for item in data
            if isinstance(item, dict)
        ] if isinstance(data, list) else []
        _WGCARDS_CACHE.set(cache_key, items, ttl_seconds=3600)
        return items

    async def get_stock(self, sku_ids: list[str]) -> list[WGCardsStock]:
        """获取 SKU 库存。"""
        unique_ids: list[str] = []
        for sku_id in sku_ids:
            normalized = str(sku_id).strip()
            if normalized and normalized not in unique_ids:
                unique_ids.append(normalized)

        stock_by_sku: dict[str, WGCardsStock] = {}
        missing: list[str] = []
        for sku_id in unique_ids:
            cached = _WGCARDS_CACHE.get(self._cache_key("stock", sku_id))
            if isinstance(cached, WGCardsStock):
                stock_by_sku[sku_id] = cached
            else:
                missing.append(sku_id)

        if missing:
            body = {"skuIds": missing}
            resp = await self._request("/api/getStock", body)
            for item in resp["data"]:
                stock = WGCardsStock(
                    item_id=str(item["itemId"]),
                    sku_id=str(item["skuId"]),
                    number=int(item["number"]),
                )
                stock_by_sku[stock.sku_id] = stock
                _WGCARDS_CACHE.set(
                    self._cache_key("stock", stock.sku_id),
                    stock,
                    ttl_seconds=60,
                )

        return [
            stock_by_sku[sku_id]
            for sku_id in unique_ids
            if sku_id in stock_by_sku
        ]

    async def get_item_and_stock(
        self,
        currency_code: str = "CNY",
        current: int = 1,
        size: int = 10,
        item_id: str = "",
        item_name: str = "",
        sku_id: str = "",
    ) -> dict:
        """获取商品信息和库存（分页）。

        传入 item_id 或 sku_id 时，供应商文档标注不受 40次/小时限制。
        """
        cache_key = self._cache_key(
            "get_item_and_stock",
            currency_code,
            current,
            size,
            item_id.strip(),
            item_name.strip(),
            sku_id.strip(),
        )
        cached = _WGCARDS_CACHE.get(cache_key)
        if isinstance(cached, dict):
            return cached

        body = {
            "appId": self.app_id,
            "currencyCode": currency_code,
            "current": current,
            "itemId": item_id,
            "itemName": item_name,
            "size": size,
        }
        if sku_id:
            body["skuId"] = sku_id
        resp = await self._request("/api/getItemAndStock", body)
        page = self._extract_page(resp, context="getItemAndStock")
        ttl = 300 if (item_id or sku_id) else 900
        _WGCARDS_CACHE.set(cache_key, page, ttl_seconds=ttl)
        return page

    async def place_order(
        self,
        service_order: str,
        sku_id: str,
        buy_num: int = 1,
        face_value: float | None = None,
        currency: str = "USD",
    ) -> WGCardsPlaceOrderResult:
        """下单购买礼品卡。

        Args:
            service_order: 外部服务订单号（唯一）
            sku_id: WGCards SKU ID
            buy_num: 购买数量
            face_value: 面值（自定义面值 SKU 必填）
            currency: 支付币种
        """
        detail = {"skuId": sku_id, "buyNum": buy_num}
        if face_value is not None:
            detail["faceValue"] = face_value

        body = {
            "userId": self.app_id,
            "accountId": self.account_id,
            "currency": currency,
            "serviceOrder": service_order,
            "detailVos": [detail],
        }
        logger.info(
            "WGCards placeOrder serviceOrder=%s sku=%s buyNum=%s faceValue=%s currency=%s",
            service_order,
            sku_id,
            buy_num,
            face_value,
            currency,
        )
        resp = await self._request("/api/placeOrder", body)
        result = self._extract_place_order_result(resp, operation="placeOrder")
        if not result.success:
            logger.warning(
                "WGCards placeOrder failed serviceOrder=%s body=%s message=%s",
                service_order,
                json.dumps(body, ensure_ascii=False),
                result.message,
            )
        return result

    async def get_order_info(
        self,
        current: int = 1,
        size: int = 10,
    ) -> dict:
        """获取历史订单列表。"""
        body = {
            "uesrId": self.app_id,
            "current": current,
            "size": size,
        }
        resp = await self._request("/api/getOrderInfo", body)
        return resp["data"]

    async def get_order_detail(self, order_id: str) -> tuple[WGCardsOrderDetail, list[WGCardsOrderLine]]:
        """获取订单详情。

        Returns:
            (订单主信息, 订单明细列表)
        """
        body = {
            "userId": self.app_id,
            "orderId": order_id,
        }
        resp = await self._request("/api/getOrderInfoAndDetail", body)
        data = resp["data"]

        first = data["firstTo"]
        order_detail = WGCardsOrderDetail(
            order_id=first["orderId"],
            request_id=first.get("requestId", ""),
            order_status=first["orderStatus"],
            delivery_status=first["deliveryStatus"],
            order_way=first["orderWay"],
            create_time=first["createTime"],
            total_amount=first["totalAmount"],
            cur=first.get("cur", ""),
            order_direct_status=first.get("orderDirectStatus"),
        )

        lines = [
            WGCardsOrderLine(
                sku_id=line["skuId"],
                sku_name=line["skuName"],
                spu_name=line["spuName"],
                is_end=line["isEnd"],
                buy_num=line["buyNum"],
                face_value=line["faceValue"],
                send_out_num=line["sendOutNum"],
                original_price=line["originalPrice"],
                original_price_cur=line["originalPriceCur"],
                actual_paid_price=line["actualPaidPrice"],
                actual_paid_price_cur=line["actualPaidPriceCur"],
                discount_ratio_str=line["discountRatioStr"],
            )
            for line in data.get("secondTos", [])
        ]

        return order_detail, lines

    async def get_buy_card(
        self,
        order_id: str,
        current: int = 1,
        size: int = 200,
    ) -> list[WGCardsCard]:
        """获取订单卡密。"""
        body = {
            "userId": self.app_id,
            "orderId": order_id,
            "current": current,
            "size": size,
        }
        resp = await self._request("/api/getBuyCard", body)
        records = self._extract_card_records(resp, context="getBuyCard")
        return [
            WGCardsCard(
                sku_id=str(rec.get("skuId", "")),
                card=str(rec.get("card", "")),
                pin_code=str(rec.get("pinCode") or rec.get("pin_code") or ""),
                sn_code=str(rec.get("snCode") or rec.get("sn_code") or ""),
            )
            for rec in records
            if rec.get("card")
        ]

    # --------------------------------------------------------------------------
    # 轮询辅助
    # --------------------------------------------------------------------------

    async def wait_for_delivery(
        self,
        order_id: str,
        timeout_seconds: int = 300,
        poll_interval: int = 5,
    ) -> WGCardsOrderDetail:
        """轮询等待订单发货完成。

        Args:
            order_id: WGCards 订单号
            timeout_seconds: 超时时间（秒）
            poll_interval: 轮询间隔（秒）

        Returns:
            最终订单状态

        Raises:
            WGCardsTimeoutError: 超时未完成
        """
        start = time.time()
        while time.time() - start < timeout_seconds:
            order_detail, _ = await self.get_order_detail(order_id)
            if order_detail.delivery_status == 3:
                return order_detail
            await self._retry_delay(0)
        raise WGCardsTimeoutError(f"Order {order_id} delivery timeout after {timeout_seconds}s")

    async def poll_and_fetch_cards(
        self,
        order_id: str,
        timeout_seconds: int = 300,
        poll_interval: int = 5,
    ) -> list[WGCardsCard]:
        """轮询等待发货完成后获取卡密。"""
        await self.wait_for_delivery(order_id, timeout_seconds, poll_interval)
        return await self.get_buy_card(order_id)
