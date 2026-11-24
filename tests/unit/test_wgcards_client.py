"""WGCards client caching/rate-limit helpers."""

from __future__ import annotations

import pytest

from app.services.wgcards_client import WGCardsClient, _retry_after_seconds


def make_client() -> WGCardsClient:
    return WGCardsClient(
        host="https://api.wgcards.com",
        app_id="test_app_id_1234",
        account_id="test_account",
        app_key="1234567890123456",
    )


def test_retry_after_seconds_from_supplier_message() -> None:
    assert (
        _retry_after_seconds(
            "Request frequency exceeds limit, please retry after 21 seconds"
        )
        == 21
    )


def test_normalize_item_accepts_sku_infos() -> None:
    client = make_client()

    item = client._normalize_item(  # noqa: SLF001
        {
            "itemId": "item-1",
            "itemName": "Steam",
            "skuInfos": [
                {
                    "skuId": "sku-1",
                    "skuName": "Steam 10 USD",
                    "minFaceValue": 10,
                    "maxFaceValue": 10,
                }
            ],
        }
    )

    assert item["skus"] == [
        {
            "skuId": "sku-1",
            "skuName": "Steam 10 USD",
            "minFaceValue": 10,
            "maxFaceValue": 10,
        }
    ]


@pytest.mark.asyncio
async def test_lookup_sku_prefers_exact_item_and_stock_lookup(monkeypatch) -> None:
    client = make_client()
    calls: list[dict[str, object]] = []

    async def fake_get_item_and_stock(**kwargs):
        calls.append(kwargs)
        return {
            "records": [
                {
                    "itemId": "item-1",
                    "itemName": "Steam",
                    "spuType": 2,
                    "skus": [
                        {
                            "skuId": "1732448182478213574",
                            "skuName": "Steam Code",
                            "minFaceValue": 10,
                            "maxFaceValue": 10,
                            "skuPrice": 9.5,
                            "skuPriceCurrency": "USD",
                            "stock": 20,
                        }
                    ],
                }
            ]
        }

    async def fail_get_item(**_kwargs):
        raise AssertionError("lookup_sku should not scan getItem when exact SKU matches")

    monkeypatch.setattr(client, "get_item_and_stock", fake_get_item_and_stock)
    monkeypatch.setattr(client, "get_item", fail_get_item)

    found = await client.lookup_sku(
        "1732448182478213574",
        currency_code="USD",
        include_item_id=True,
    )

    assert calls == [
        {
            "currency_code": "USD",
            "current": 1,
            "size": 10,
            "sku_id": "1732448182478213574",
        }
    ]
    assert found == {
        "itemId": "item-1",
        "itemName": "Steam",
        "spuType": 2,
        "skus": [
            {
                "skuId": "1732448182478213574",
                "skuName": "Steam Code",
                "minFaceValue": 10,
                "maxFaceValue": 10,
                "skuPrice": 9.5,
                "skuPriceCurrency": "USD",
                "stock": 20,
            }
        ],
    }
