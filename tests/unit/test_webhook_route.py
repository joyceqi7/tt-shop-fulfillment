from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import webhooks
from app.auth import webhook_signer
from app.config import Settings


class _FakeResult:
    def scalar_one_or_none(self) -> int:
        return 123


class _FakeDb:
    def __init__(self) -> None:
        self.committed = False

    async def execute(self, stmt: object) -> _FakeResult:
        return _FakeResult()

    async def commit(self) -> None:
        self.committed = True


class _FakeArq:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, int]] = []

    async def enqueue_job(self, name: str, event_id: int) -> None:
        self.jobs.append((name, event_id))


def _settings(secret: str) -> Settings:
    return Settings(
        tts_app_key="test-key",
        tts_app_secret=secret,
        tts_refresh_token="refresh",
        fernet_key="test-fernet-key",
    )


@pytest.mark.parametrize("header_name", ["X-Tt-Signature", "X-Tts-Signature"])
def test_tts_webhook_accepts_current_and_legacy_signature_headers(
    header_name: str,
) -> None:
    secret = "webhook-secret"
    payload = {
        "type": "ORDER_STATUS_CHANGE",
        "tts_notification_id": f"n-{header_name}",
        "data": {"shop_id": "shop-1", "order_id": "order-1"},
    }
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = webhook_signer.sign(body, secret)
    db = _FakeDb()
    arq = _FakeArq()

    app = FastAPI()
    app.include_router(webhooks.router, prefix="/webhooks")
    app.dependency_overrides[webhooks.get_settings_dep] = lambda: _settings(secret)
    app.dependency_overrides[webhooks.get_db] = lambda: db
    app.dependency_overrides[webhooks.get_arq_pool] = lambda: arq

    with TestClient(app) as client:
        response = client.post(
            "/webhooks/tts",
            content=body,
            headers={"Content-Type": "application/json", header_name: signature},
        )

    assert response.status_code == 200
    assert response.json() == {"code": 0, "message": "queued", "event_id": 123}
    assert db.committed is True
    assert arq.jobs == [("fulfill_order", 123)]
