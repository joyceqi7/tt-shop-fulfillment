"""TTS 业务客户端单元测试。

用 pytest-httpx 拦截 HTTP，验证：
- 参数自动补全（app_key / timestamp / version / sign）
- `x-tts-access-token` 走 header 不入签名
- shop_cipher 入 query 且参与签名
- code != 0 抛 TTSApiError
- HTTP 5xx 抛 is_retryable=True 的 TTSApiError
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import httpx
import pytest
from pytest_httpx import HTTPXMock

from app.auth.signer import sign
from app.config import Settings
from app.services.tts_client import TTSApiError, TTSClient


@dataclass
class StubTokenManager:
    token: str = "stub-access-token"

    async def get_access_token(self) -> str:
        return self.token


@pytest.fixture
def settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        tts_app_key="test_app_key",
        tts_app_secret="test_app_secret",
        tts_refresh_token="rt",
        tts_api_base="https://api.example.com",
        tts_auth_base="https://auth.example.com",
        fernet_key="dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=",
    )


@pytest.fixture
def client(settings: Settings) -> TTSClient:
    # 不传 http_client，让 TTSClient 每次请求自建 AsyncClient，pytest-httpx 才能接管
    return TTSClient(settings, StubTokenManager())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_get_authorized_shops_injects_token_and_signature(
    httpx_mock: HTTPXMock, client: TTSClient, settings: Settings
) -> None:
    httpx_mock.add_response(
        url=re.compile(r"https://api\.example\.com/authorization/202309/shops.*"),
        match_headers={"x-tts-access-token": "stub-access-token"},
        json={"code": 0, "data": {"shops": [{"id": "1"}]}, "message": "ok"},
    )

    shops = await client.get_authorized_shops()
    assert shops == [{"id": "1"}]

    req = httpx_mock.get_requests()[0]
    qs = dict(req.url.params)
    assert qs["app_key"] == settings.tts_app_key
    assert qs["version"] == "202309"
    assert "timestamp" in qs
    assert "sign" in qs
    assert "access_token" not in qs

    expected = sign(
        "/authorization/202309/shops",
        {k: v for k, v in qs.items() if k != "sign"},
        settings.tts_app_secret,
        None,
    )
    assert qs["sign"] == expected


@pytest.mark.asyncio
async def test_get_order_list_puts_shop_cipher_in_query(
    httpx_mock: HTTPXMock, client: TTSClient
) -> None:
    httpx_mock.add_response(
        url=re.compile(r"https://api\.example\.com/order/202309/orders/search.*"),
        json={"code": 0, "data": {"orders": [], "total_count": 0}, "message": "ok"},
    )

    await client.get_order_list(
        "cipher_xyz", page_size=20, order_status="AWAITING_SHIPMENT"
    )

    req = httpx_mock.get_requests()[0]
    qs = dict(req.url.params)
    assert qs["shop_cipher"] == "cipher_xyz"
    assert qs["page_size"] == "20"

    body = json.loads(req.content.decode())
    assert body == {"order_status": "AWAITING_SHIPMENT"}


@pytest.mark.asyncio
async def test_business_error_code_raises(
    httpx_mock: HTTPXMock, client: TTSClient
) -> None:
    httpx_mock.add_response(
        url=re.compile(r"https://api\.example\.com/authorization/202309/shops.*"),
        json={
            "code": 12001001,
            "message": "Access token invalid",
            "request_id": "abc",
        },
    )

    with pytest.raises(TTSApiError) as excinfo:
        await client.get_authorized_shops()

    assert excinfo.value.code == 12001001
    assert excinfo.value.request_id == "abc"
    assert not excinfo.value.is_retryable


@pytest.mark.asyncio
async def test_http_5xx_is_retryable(httpx_mock: HTTPXMock, client: TTSClient) -> None:
    httpx_mock.add_response(
        url=re.compile(r"https://api\.example\.com/authorization/202309/shops.*"),
        status_code=502,
        json={"code": -1, "message": "bad gateway"},
    )

    with pytest.raises(TTSApiError) as excinfo:
        await client.get_authorized_shops()

    assert excinfo.value.http_status == 502
    assert excinfo.value.is_retryable
