"""Admin /admin/reship HTTP endpoint smoke test。

4 个场景：
1. 无 X-Admin-Token → 403
2. 错误 token → 403
3. 正确 token + 合法 body → 200 queued，event 入库，arq 队列 +1
4. 同一 order_id 连续 reship 两次 → 生成 2 个独立 event + 2 个 job
   （因为每次 dedup_key 不同，不会被 webhook 幂等机制吃掉）
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg  # noqa: E402
import redis as sync_redis  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.main import app  # noqa: E402
from app.config import get_settings  # noqa: E402


def _sync_dsn(async_dsn: str) -> str:
    return async_dsn.replace("postgresql+asyncpg://", "postgresql://")


def _query_events_for_order(dsn: str, order_id: str) -> list[tuple]:
    with psycopg.connect(_sync_dsn(dsn), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, event_type, dedup_key, status "
                "FROM webhook_events WHERE order_id = %s ORDER BY id",
                (order_id,),
            )
            return cur.fetchall()


def _cleanup_events(dsn: str, order_id: str) -> None:
    with psycopg.connect(_sync_dsn(dsn), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM webhook_events WHERE order_id = %s", (order_id,))


def _queue_len(redis_url: str) -> int:
    r = sync_redis.Redis.from_url(redis_url)
    try:
        return r.zcard("arq:queue")
    finally:
        r.close()


def main() -> None:
    settings = get_settings()
    assert settings.admin_token, "ADMIN_TOKEN 未配置，无法跑 smoke"

    order_id = f"smoke-reship-{uuid.uuid4().hex[:8]}"
    _cleanup_events(settings.database_url, order_id)

    with TestClient(app) as client:
        queue_before = _queue_len(settings.redis_url)

        # Case 1: 无 token
        print("=" * 60)
        print("Case 1: 无 X-Admin-Token → 期望 403")
        print("=" * 60)
        r = client.post("/admin/reship", json={"order_id": order_id})
        print(f"  status={r.status_code} body={r.json()}")
        assert r.status_code == 403
        print("  OK")
        print()

        # Case 2: 错 token
        print("=" * 60)
        print("Case 2: 错误 X-Admin-Token → 期望 403")
        print("=" * 60)
        r = client.post(
            "/admin/reship",
            json={"order_id": order_id},
            headers={"X-Admin-Token": "wrong-token"},
        )
        print(f"  status={r.status_code} body={r.json()}")
        assert r.status_code == 403
        print("  OK")
        print()

        # Case 3: 正确 token
        print("=" * 60)
        print("Case 3: 正确 token + 合法 body → 期望 200 queued + event 入库 + 队列 +1")
        print("=" * 60)
        r = client.post(
            "/admin/reship",
            json={"order_id": order_id, "shop_id": "smoke-shop"},
            headers={"X-Admin-Token": settings.admin_token},
        )
        print(f"  status={r.status_code} body={r.json()}")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "queued"
        assert body["order_id"] == order_id
        assert isinstance(body["event_id"], int) and body["event_id"] > 0
        assert body["job_id"]

        events = _query_events_for_order(settings.database_url, order_id)
        assert len(events) == 1
        assert events[0][1] == "ADMIN_RESHIP"
        assert events[0][2].startswith(f"admin-reship:{order_id}:")
        print(f"  OK event_id={events[0][0]} dedup_key={events[0][2]}")

        queue_after_c3 = _queue_len(settings.redis_url)
        assert queue_after_c3 == queue_before + 1, (
            f"arq queue len {queue_before}→{queue_after_c3}, expected +1"
        )
        print(f"  OK arq queue {queue_before} → {queue_after_c3}")
        print()

        # Case 4: 同 order_id 再 reship 一次 → 独立 event
        print("=" * 60)
        print("Case 4: 同 order_id 再次 reship → 期望第二个独立 event")
        print("=" * 60)
        r2 = client.post(
            "/admin/reship",
            json={"order_id": order_id, "shop_id": "smoke-shop"},
            headers={"X-Admin-Token": settings.admin_token},
        )
        print(f"  status={r2.status_code} body={r2.json()}")
        assert r2.status_code == 200
        body2 = r2.json()
        assert body2["event_id"] != body["event_id"]

        events_after = _query_events_for_order(settings.database_url, order_id)
        assert len(events_after) == 2
        assert events_after[0][2] != events_after[1][2], "dedup_key 必须各自不同"
        print(f"  OK 两个 event dedup_key 各自独立: {events_after[0][2]} / {events_after[1][2]}")

        queue_after_c4 = _queue_len(settings.redis_url)
        assert queue_after_c4 == queue_before + 2
        print(f"  OK arq queue {queue_after_c3} → {queue_after_c4}")
        print()

    _cleanup_events(settings.database_url, order_id)

    print("=" * 60)
    print("全部通过 OK — /admin/reship 鉴权 + 入库 + 入队 + 多次幂等独立 贯通")
    print("=" * 60)


if __name__ == "__main__":
    main()
