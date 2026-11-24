"""TikTok Shop Open API 业务客户端。

覆盖 Onboarding 要求的 4 个核心 API：
- get_authorized_shops  : 拿 shop_cipher（启动时一次 + 授权变更时）
- get_order_list        : 兜底对账拉订单列表
- get_order_detail      : webhook 命中后拉单条详情
- redeem_info_callback  : 发货（回传 redeem_info）

统一处理：
- 签名（参与签名的参数：app_key / timestamp / version / shop_cipher / 业务参数）
- `x-tts-access-token` header（TT v2 API 要求放 header，不参与签名）
- 代理（开发机用 Clash，生产机裸直连）
- 错误分类（先写基础 4XX vs 5XX，后续在 retry_alert 里完善重试策略）

注意 endpoint 路径：
- 授权接口在 /authorization/202309/*
- 订单接口在 /order/202309/* (list)、/order/202507/* (detail)
- 发货回调在 /fulfillment/202601/redeem_info/callback
  Body: {"order_id": "...", "order_info_list": [
      {"order_line_id": "...", "redeem_info": {"redeem_type": "CODE", "redeem_data": "..."},
       "source_unique_id": "..."}
  ]}
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

import httpx

from app.auth.signer import current_timestamp, sign
from app.auth.token_manager import TokenManager
from app.config import Settings


logger = logging.getLogger(__name__)


class TTSApiError(RuntimeError):
    """TT 业务 API 返回 code != 0 或 HTTP 非 2XX。"""

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        request_id: str | None = None,
        http_status: int | None = None,
        response_body: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.request_id = request_id
        self.http_status = http_status
        self.response_body = response_body

    @property
    def is_retryable(self) -> bool:
        """先给最粗的判断，后续在 retry_alert 里按 TT 错误码细化。"""
        if self.http_status is not None and self.http_status >= 500:
            return True
        if self.http_status == 429:
            return True
        return False


class TTSClient:
    DEFAULT_VERSION = "202309"

    def __init__(
        self,
        settings: Settings,
        token_manager: TokenManager,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._tm = token_manager
        self._http = http_client

    async def _request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
        shop_cipher: str | None = None,
        version: str = DEFAULT_VERSION,
        require_token: bool = True,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "app_key": self._settings.tts_app_key,
            "timestamp": current_timestamp(),
            "version": version,
        }
        if shop_cipher:
            params["shop_cipher"] = shop_cipher
        if query:
            for k, v in query.items():
                if v is None:
                    continue
                params[k] = v

        body_str: str | None = None
        if body is not None:
            body_str = json.dumps(body, separators=(",", ":"), ensure_ascii=False)

        params["sign"] = sign(path, params, self._settings.tts_app_secret, body_str)

        headers: dict[str, str] = {}
        if body_str is not None:
            headers["Content-Type"] = "application/json"
        if require_token:
            headers["x-tts-access-token"] = await self._tm.get_access_token()

        url = f"{self._settings.tts_api_base}{path}"

        client = self._http or httpx.AsyncClient(
            proxy=self._settings.tts_http_proxy or None,
            trust_env=False,
            timeout=30,
        )
        owns_client = self._http is None
        try:
            resp = await client.request(
                method,
                url,
                params=params,
                content=body_str,
                headers=headers,
            )
        finally:
            if owns_client:
                await client.aclose()

        payload: Any
        try:
            payload = resp.json()
        except ValueError:
            payload = {"raw": resp.text}

        if resp.status_code >= 400:
            raise TTSApiError(
                f"HTTP {resp.status_code} from {path}: {payload}",
                http_status=resp.status_code,
                response_body=payload,
            )

        if isinstance(payload, dict) and payload.get("code") not in (0, None):
            raise TTSApiError(
                f"{path} returned code={payload.get('code')} message={payload.get('message')!r}",
                code=payload.get("code"),
                request_id=payload.get("request_id"),
                http_status=resp.status_code,
                response_body=payload,
            )

        return payload if isinstance(payload, dict) else {"data": payload}

    # --- 业务方法 ---

    async def get_authorized_shops(self) -> list[dict[str, Any]]:
        """拉所有授权的店铺，启动时调一次。"""
        payload = await self._request("GET", "/authorization/202309/shops")
        return payload.get("data", {}).get("shops", [])

    async def get_order_list(
        self,
        shop_cipher: str,
        *,
        page_size: int = 50,
        page_token: str | None = None,
        order_status: str | None = None,
        create_time_ge: int | None = None,
        create_time_lt: int | None = None,
        update_time_ge: int | None = None,
        update_time_lt: int | None = None,
    ) -> dict[str, Any]:
        query: dict[str, Any] = {"page_size": page_size}
        if page_token:
            query["page_token"] = page_token

        body: dict[str, Any] = {}
        if order_status:
            body["order_status"] = order_status
        if create_time_ge is not None:
            body["create_time_ge"] = create_time_ge
        if create_time_lt is not None:
            body["create_time_lt"] = create_time_lt
        if update_time_ge is not None:
            body["update_time_ge"] = update_time_ge
        if update_time_lt is not None:
            body["update_time_lt"] = update_time_lt

        return await self._request(
            "POST",
            "/order/202309/orders/search",
            query=query,
            body=body,
            shop_cipher=shop_cipher,
        )

    async def get_order_detail(
        self, shop_cipher: str, order_ids: list[str]
    ) -> dict[str, Any]:
        """批量拉订单详情。TT 202507 接口。"""
        return await self._request(
            "GET",
            "/order/202507/orders",
            query={"ids": ",".join(order_ids)},
            shop_cipher=shop_cipher,
            version="202507",
        )

    async def search_products(
        self,
        shop_cipher: str,
        *,
        page_size: int = 50,
        page_token: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """拉 TT 商品列表（只读）。

        scope: seller.product.basic

        Product API 的字段在不同店铺/版本可能略有差异，所以这里保持原始
        payload 返回，解析集中放在 product_sync 服务里。
        """
        query: dict[str, Any] = {"page_size": page_size}
        if page_token:
            query["page_token"] = page_token

        body: dict[str, Any] = {}
        if status:
            body["status"] = status

        return await self._request(
            "POST",
            "/product/202309/products/search",
            query=query,
            body=body,
            shop_cipher=shop_cipher,
        )

    async def get_product_detail(
        self,
        shop_cipher: str,
        product_id: str,
    ) -> dict[str, Any]:
        """拉单个 TT 商品详情（只读）。

        scope: seller.product.basic
        """
        return await self._request(
            "GET",
            f"/product/202309/products/{product_id}",
            shop_cipher=shop_cipher,
        )

    async def redeem_info_callback(
        self,
        shop_cipher: str,
        order_id: str,
        order_info_list: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """发货：回传 redeem_info（TT v202601 接口）。

        path : POST /fulfillment/202601/redeem_info/callback
        body : {
                 "order_id": "<TT order id>",
                 "order_info_list": [
                   {
                     "order_line_id": "<TT order line id>",
                     "redeem_info": {"redeem_type": "CODE", "redeem_data": "<明文卡密>"},
                     "source_unique_id": "<本地卡密唯一 ID，用于 TT 侧幂等去重>"
                   },
                   ...
                 ]
               }

        scope: seller.redeem_info.write
        """
        return await self._request(
            "POST",
            "/fulfillment/202601/redeem_info/callback",
            body={"order_id": order_id, "order_info_list": order_info_list},
            shop_cipher=shop_cipher,
            version="202601",
        )
