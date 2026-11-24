"""OAuth 授权 smoke test。

5 个场景：
1. state 生命周期：issue → consume 成功 → 再 consume 失败（防重放） + 未知 state 失败
2. /oauth/tts/start：返回 302 + Location 带 service_id + state
3. /oauth/tts/callback 成功路径：mock TT token/get + shops → 200 HTML + DB 落 token + tts_shops
4. /oauth/tts/callback 异常：
   - state 不带 → 400
   - state 错误 → 400
   - 重复消费 state → 400
   - 不带 code → 400
5. /oauth/tts/callback 部分失败：TT token/get 返回 code!=0 → 502 HTML，DB 未污染

原则：
- 不破坏 dev 环境的真实 tts_tokens：跑 smoke 前 snapshot [app_key=settings.tts_app_key] 的行，跑完 restore
- tts_shops 用 test-only shop_id 前缀，跑完删干净
- 不调真实 TT API：通过 monkeypatch TokenManager._call_token_api + shop_sync.upsert_shops_from_tt
"""

from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from redis.asyncio import Redis  # noqa: E402

from app.auth import token_manager as tm_mod  # noqa: E402
from app.auth.crypto import decrypt  # noqa: E402
from app.auth.token_manager import RefreshedToken  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.models.shop import TTSShop  # noqa: E402
from app.services.oauth_state import (  # noqa: E402
    STATE_KEY_TMPL,
    consume_state,
    issue_state,
)


TEST_SERVICE_ID = "smoke-service-id"
TEST_SHOP_ID_PREFIX = "oauth-smoke-shop-"


# --- DB helpers（同步 psycopg，避免 smoke 里用 async engine 混乱） ---


def _sync_dsn(async_dsn: str) -> str:
    return async_dsn.replace("postgresql+asyncpg://", "postgresql://")


def _snapshot_token_row(app_key: str) -> dict[str, Any] | None:
    dsn = _sync_dsn(get_settings().database_url)
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT access_token_encrypted, access_token_expire_at, "
            "refresh_token_encrypted, refresh_token_expire_at, "
            "seller_name, open_id, version, created_at, updated_at "
            "FROM tts_tokens WHERE app_key = %s",
            (app_key,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        cols = [
            "access_token_encrypted",
            "access_token_expire_at",
            "refresh_token_encrypted",
            "refresh_token_expire_at",
            "seller_name",
            "open_id",
            "version",
            "created_at",
            "updated_at",
        ]
        return dict(zip(cols, row))


def _restore_token_row(app_key: str, snap: dict[str, Any] | None) -> None:
    dsn = _sync_dsn(get_settings().database_url)
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM tts_tokens WHERE app_key = %s", (app_key,))
        if snap is None:
            return
        cur.execute(
            "INSERT INTO tts_tokens(app_key, access_token_encrypted, access_token_expire_at, "
            "refresh_token_encrypted, refresh_token_expire_at, seller_name, open_id, "
            "version, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                app_key,
                snap["access_token_encrypted"],
                snap["access_token_expire_at"],
                snap["refresh_token_encrypted"],
                snap["refresh_token_expire_at"],
                snap["seller_name"],
                snap["open_id"],
                snap["version"],
                snap["created_at"],
                snap["updated_at"],
            ),
        )


def _cleanup_test_shops() -> None:
    dsn = _sync_dsn(get_settings().database_url)
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM tts_shops WHERE shop_id LIKE %s",
            (TEST_SHOP_ID_PREFIX + "%",),
        )


def _fetch_token_row(app_key: str) -> dict[str, Any] | None:
    return _snapshot_token_row(app_key)


def _fetch_test_shops() -> list[dict[str, Any]]:
    dsn = _sync_dsn(get_settings().database_url)
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT shop_id, shop_cipher, shop_name, region, seller_type, seller_base_region "
            "FROM tts_shops WHERE shop_id LIKE %s ORDER BY shop_id",
            (TEST_SHOP_ID_PREFIX + "%",),
        )
        rows = cur.fetchall()
        cols = [
            "shop_id",
            "shop_cipher",
            "shop_name",
            "region",
            "seller_type",
            "seller_base_region",
        ]
        return [dict(zip(cols, r)) for r in rows]


# --- 测试场景 ---


async def case_1_state_lifecycle() -> None:
    print("=" * 60)
    print("Case 1: state 生命周期（issue → consume → 重放失败 → 未知 state 失败）")
    print("=" * 60)
    redis = Redis.from_url(get_settings().redis_url)
    try:
        state = await issue_state(redis, ttl_seconds=60)
        assert state and len(state) >= 40, f"state 太短: {state!r}"
        assert await consume_state(redis, state) is True, "首次 consume 应成功"
        assert await consume_state(redis, state) is False, "重放应失败"
        assert await consume_state(redis, "not-a-real-state") is False, "未知 state 应失败"
        assert await consume_state(redis, "") is False, "空 state 应失败"
        print("  ✅ state lifecycle OK")
    finally:
        await redis.aclose()


def case_2_start_redirect(client: TestClient) -> str:
    print()
    print("=" * 60)
    print("Case 2: /oauth/tts/start 返回 302 带 service_id + state")
    print("=" * 60)
    resp = client.get("/oauth/tts/start", follow_redirects=False)
    assert resp.status_code == 302, f"期望 302，实际 {resp.status_code}: {resp.text}"
    loc = resp.headers["location"]
    qs = parse_qs(urlparse(loc).query)
    assert qs.get("service_id") == [TEST_SERVICE_ID], f"service_id 不对: {qs}"
    state = qs["state"][0]
    assert state, "没带 state"
    print(f"  ✅ 302 location: {loc[:80]}...")
    print(f"  ✅ state: {state[:16]}...")
    return state


def case_3_callback_success(client: TestClient) -> None:
    print()
    print("=" * 60)
    print("Case 3: /callback 成功路径 → 200 HTML + token 落库 + shops upsert")
    print("=" * 60)

    # 发起 start 拿 state
    start = client.get("/oauth/tts/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]

    resp = client.get(
        "/oauth/tts/callback",
        params={"code": "smoke-auth-code-001", "state": state},
    )
    assert resp.status_code == 200, f"期望 200，实际 {resp.status_code}: {resp.text[:200]}"
    body = resp.text
    assert "授权成功" in body, "HTML 应包含'授权成功'"
    assert "SMOKE SELLER" in body, f"HTML 应包含 seller_name，实际: {body[:300]}"
    assert TEST_SHOP_ID_PREFIX + "A" in body, "HTML 应列出 test shop A"
    assert TEST_SHOP_ID_PREFIX + "B" in body, "HTML 应列出 test shop B"
    print(f"  ✅ HTML 200 contains '授权成功' + seller + 2 shops")

    # 验 DB
    row = _fetch_token_row(get_settings().tts_app_key)
    assert row is not None, "tts_tokens 应落库"
    assert row["seller_name"] == "SMOKE SELLER"
    assert row["open_id"] == "open-smoke-001"
    assert decrypt(row["access_token_encrypted"]) == "smoke-access-token-xyz"
    assert decrypt(row["refresh_token_encrypted"]) == "smoke-refresh-token-abc"
    print(f"  ✅ tts_tokens row: seller={row['seller_name']} expire_at={row['access_token_expire_at']}")

    shops = _fetch_test_shops()
    assert len(shops) == 2, f"tts_shops 应有 2 条 smoke 店，实际 {len(shops)}"
    assert shops[0]["shop_cipher"] == "cipher-A"
    assert shops[1]["shop_cipher"] == "cipher-B"
    print(f"  ✅ tts_shops upserted: {[s['shop_id'] for s in shops]}")


def case_4_callback_errors(client: TestClient) -> None:
    print()
    print("=" * 60)
    print("Case 4: /callback 异常路径（state/code 各种姿势出错）")
    print("=" * 60)

    # 不带 state
    r = client.get("/oauth/tts/callback", params={"code": "x"})
    assert r.status_code == 400, f"缺 state 应 400，实际 {r.status_code}"
    assert "invalid or expired state" in r.text.lower(), r.text
    print("  ✅ 缺 state → 400")

    # 带错误 state
    r = client.get(
        "/oauth/tts/callback", params={"code": "x", "state": "bogus-state"}
    )
    assert r.status_code == 400
    print("  ✅ 错误 state → 400")

    # 重放已消费的 state
    start = client.get("/oauth/tts/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    # 第一次消费
    client.get("/oauth/tts/callback", params={"code": "x", "state": state})
    # 重放
    r2 = client.get("/oauth/tts/callback", params={"code": "x", "state": state})
    assert r2.status_code == 400, f"state 重放应 400，实际 {r2.status_code}"
    print("  ✅ state 重放 → 400")

    # 不带 code（state 合法但缺 code）
    start = client.get("/oauth/tts/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    r = client.get("/oauth/tts/callback", params={"state": state})
    assert r.status_code == 400, f"缺 code 应 400，实际 {r.status_code}"
    assert "missing auth code" in r.text.lower()
    print("  ✅ 缺 code → 400（且 state 未被消费，后面可以再用）")


def case_5_callback_token_api_fail(client: TestClient, *, restore_fake) -> None:
    print()
    print("=" * 60)
    print("Case 5: TT token/get 返回错误 → 502 HTML，DB 未污染")
    print("=" * 60)

    # snapshot 当前 token 行状态
    settings = get_settings()
    before = _fetch_token_row(settings.tts_app_key)

    # 注入一个会抛 RuntimeError 的 fake _call_token_api
    async def bad_call_token_api(self, grant_type, **extra):  # type: ignore[no-untyped-def]
        raise RuntimeError("TT token api failed: code=36004001 message='invalid auth_code'")

    tm_mod.TokenManager._call_token_api = bad_call_token_api

    try:
        start = client.get("/oauth/tts/start", follow_redirects=False)
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
        r = client.get(
            "/oauth/tts/callback",
            params={"code": "bad-auth-code", "state": state},
        )
        assert r.status_code == 502, f"期望 502，实际 {r.status_code}: {r.text[:200]}"
        assert "授权失败" in r.text and "Token 交换失败" in r.text
        assert "invalid auth_code" in r.text
        print("  ✅ 502 HTML 包含错误信息")

        # DB 状态未变
        after = _fetch_token_row(settings.tts_app_key)
        assert after == before, "DB token 行在 token api 失败路径上不应变更"
        print("  ✅ DB 未被污染")
    finally:
        # restore 回正常 fake，后面可能还要用
        restore_fake()


# --- main ---


async def main() -> None:
    settings = get_settings()

    print("=" * 60)
    print("TT Shop OAuth smoke —— 准备环境")
    print("=" * 60)

    token_backup = _snapshot_token_row(settings.tts_app_key)
    print(f"  tts_tokens[{settings.tts_app_key}] 备份：{'有' if token_backup else '空'}")

    # 临时改 settings.tts_service_id（Pydantic BaseSettings 默认可 mutate）
    original_service_id = settings.tts_service_id
    settings.tts_service_id = TEST_SERVICE_ID

    # 注入 fake _call_token_api 和 upsert_shops_from_tt
    original_call_token_api = tm_mod.TokenManager._call_token_api

    async def fake_call_token_api(self, grant_type, **extra):  # type: ignore[no-untyped-def]
        assert grant_type == "authorized_code", f"oauth 只应调 authorized_code，实际 {grant_type}"
        assert extra.get("auth_code") == "smoke-auth-code-001", f"意外 auth_code={extra!r}"
        return RefreshedToken(
            access_token="smoke-access-token-xyz",
            access_token_expire_at=int(time.time()) + 7200,
            refresh_token="smoke-refresh-token-abc",
            refresh_token_expire_at=int(time.time()) + 30 * 86400,
            seller_name="SMOKE SELLER",
            open_id="open-smoke-001",
        )

    tm_mod.TokenManager._call_token_api = fake_call_token_api

    from app.api.routes import oauth as oauth_mod
    original_upsert = oauth_mod.upsert_shops_from_tt

    async def fake_upsert(*, client, session_factory):  # type: ignore[no-untyped-def]
        # 模拟真实 upsert 行为：直接写 DB（让 Case 3 能验到落库）
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        shops_data = [
            dict(
                shop_id=TEST_SHOP_ID_PREFIX + "A",
                shop_cipher="cipher-A",
                shop_name="Smoke Shop A",
                region="US",
                seller_type="LOCAL",
                seller_base_region="US",
            ),
            dict(
                shop_id=TEST_SHOP_ID_PREFIX + "B",
                shop_cipher="cipher-B",
                shop_name="Smoke Shop B",
                region="US",
                seller_type="LOCAL",
                seller_base_region="US",
            ),
        ]
        async with session_factory() as session:
            for s in shops_data:
                stmt = (
                    pg_insert(TTSShop)
                    .values(**s)
                    .on_conflict_do_update(
                        index_elements=["shop_id"],
                        set_={k: v for k, v in s.items() if k != "shop_id"},
                    )
                )
                await session.execute(stmt)
            await session.commit()
        # 返对象，让 oauth route 能渲染
        return [TTSShop(**s) for s in shops_data]

    oauth_mod.upsert_shops_from_tt = fake_upsert

    # cleanup 残留
    _cleanup_test_shops()

    def restore_fake():
        tm_mod.TokenManager._call_token_api = fake_call_token_api

    try:
        await case_1_state_lifecycle()

        from app.api.main import app
        with TestClient(app) as client:
            case_2_start_redirect(client)
            # Case 3 之前要保证 DB 干净的 token 状态不重要——fake_call_token_api 里的 access_token 是固定值
            case_3_callback_success(client)
            case_4_callback_errors(client)
            case_5_callback_token_api_fail(client, restore_fake=restore_fake)

        print()
        print("=" * 60)
        print("全部通过 ✅")
        print("=" * 60)
    finally:
        # restore
        tm_mod.TokenManager._call_token_api = original_call_token_api
        oauth_mod.upsert_shops_from_tt = original_upsert
        settings.tts_service_id = original_service_id
        _cleanup_test_shops()
        _restore_token_row(settings.tts_app_key, token_backup)
        print("  清理完成（dev tts_tokens 已还原）")


if __name__ == "__main__":
    asyncio.run(main())
