"""Webhook 接收器 smoke test。

用 fastapi.TestClient（自动触发 lifespan，arq pool 会真的连 Redis）：
1. 用 webhook_signer.sign 算签名，模拟 TT 推送
2. POST 一次 → 期望 200 + queued + 入 webhook_events 表 + 入 arq 队列
3. 再 POST 同样 payload → 期望 200 + duplicate（幂等）
4. POST 一次错误签名 → 期望 403
5. 清理测试数据

跑完后可以：
    arq app.workers.settings.WorkerSettings
观察 worker 把队列里的 job 拉出来处理。
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg  # noqa: E402  - 同步 psycopg，故意不走 sqlalchemy 避免跨事件循环
import redis  # noqa: E402  - 同步 redis 客户端
from fastapi.testclient import TestClient  # noqa: E402

from app.api.main import app  # noqa: E402
from app.auth import webhook_signer  # noqa: E402
from app.config import get_settings  # noqa: E402


def _sync_dsn(async_dsn: str) -> str:
    """postgresql+asyncpg://... → postgresql://..."""
    return async_dsn.replace("+asyncpg", "", 1)


def _cleanup(dsn: str, dedup_key: str) -> None:
    with psycopg.connect(_sync_dsn(dsn), autocommit=True) as conn:
        conn.execute("DELETE FROM webhook_events WHERE dedup_key = %s", (dedup_key,))


def _query_event(dsn: str, dedup_key: str) -> tuple | None:
    with psycopg.connect(_sync_dsn(dsn)) as conn:
        row = conn.execute(
            "SELECT id, status, event_type, order_id, shop_id, timestamp_tt "
            "FROM webhook_events WHERE dedup_key = %s",
            (dedup_key,),
        ).fetchone()
    return row


def _arq_queue_length(redis_url: str) -> int:
    u = urlparse(redis_url)
    r = redis.Redis(
        host=u.hostname or "localhost",
        port=u.port or 6379,
        db=int((u.path or "/0").lstrip("/") or 0),
        password=u.password,
    )
    try:
        return int(r.zcard("arq:queue"))
    finally:
        r.close()


def main() -> None:
    settings = get_settings()
    notification_id = f"smoke-{uuid.uuid4().hex[:12]}"
    payload = {
        "type": "ORDER_STATUS_CHANGE",
        "tts_notification_id": notification_id,
        "shop_id": "smoke-shop",
        "timestamp": int(time.time()),
        "data": {"order_id": "smoke-order-1", "order_status": "AWAITING_SHIPMENT"},
    }
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sig = webhook_signer.sign(body, settings.tts_app_secret)

    _cleanup(settings.database_url, notification_id)
    queue_len_before = _arq_queue_length(settings.redis_url)

    print("=" * 60)
    print("Step 1: 第一次 POST（合法签名）→ 期望 200 + queued")
    print("=" * 60)
    with TestClient(app) as client:
        r = client.post(
            "/webhooks/tts",
            content=body,
            headers={"X-Tts-Signature": sig, "Content-Type": "application/json"},
        )
        print(f"  status={r.status_code} body={r.json()}")
        assert r.status_code == 200
        assert r.json()["message"] == "queued"
        event_id = r.json()["event_id"]

        ev = _query_event(settings.database_url, notification_id)
        assert ev is not None and ev[0] == event_id
        ev_id, ev_status, ev_type, ev_order, ev_shop, ev_ts = ev
        print(f"  OK webhook_events 入库 id={ev_id} status={ev_status}")
        print(
            f"     order_id={ev_order} type={ev_type} "
            f"shop_id={ev_shop} ts={ev_ts}"
        )

        queue_len_after = _arq_queue_length(settings.redis_url)
        print(
            f"  OK arq 队列长度 {queue_len_before} → {queue_len_after}（应该 +1）"
        )
        assert queue_len_after == queue_len_before + 1

        print()
        print("=" * 60)
        print("Step 2: 同 payload 重发 → 期望 200 + duplicate（幂等）")
        print("=" * 60)
        r2 = client.post(
            "/webhooks/tts",
            content=body,
            headers={"X-Tts-Signature": sig, "Content-Type": "application/json"},
        )
        print(f"  status={r2.status_code} body={r2.json()}")
        assert r2.status_code == 200
        assert r2.json()["message"] == "duplicate"

        queue_len_dup = _arq_queue_length(settings.redis_url)
        assert queue_len_dup == queue_len_after, "重复不应再入队"
        print("  OK 队列长度不变，未重复入队")

        print()
        print("=" * 60)
        print("Step 3: 错误签名 → 期望 403")
        print("=" * 60)
        r3 = client.post(
            "/webhooks/tts",
            content=body,
            headers={"X-Tts-Signature": "deadbeef" * 8, "Content-Type": "application/json"},
        )
        print(f"  status={r3.status_code} body={r3.json()}")
        assert r3.status_code == 403

        print()
        print("=" * 60)
        print("Step 4: 缺签名 header → 期望 403")
        print("=" * 60)
        r4 = client.post(
            "/webhooks/tts",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        print(f"  status={r4.status_code} body={r4.json()}")
        assert r4.status_code == 403

    print()
    print("=" * 60)
    print("Step 5: burst 模式启 arq worker 把队列吃掉 → 期望 event 被 worker 介入")
    print("=" * 60)
    import asyncio as _asyncio
    import logging as _logging
    from arq.worker import Worker
    from app.db.session import _engine, session_factory
    from app.workers.settings import WorkerSettings

    _logging.basicConfig(level=_logging.INFO, format="  [worker] %(name)s %(message)s")

    # TestClient 的 lifespan 把 async engine 绑到了它的 loop，这里要开新 loop
    # 跑 worker，必须先清 lru_cache 让 engine 重建。真实部署里 worker 是独立
    # 进程，不会遇到这个问题。
    _engine.cache_clear()
    session_factory.cache_clear()

    async def _burst() -> None:
        w = Worker(
            functions=WorkerSettings.functions,
            redis_settings=WorkerSettings.redis_settings,
            burst=True,
            poll_delay=0.2,
            max_burst_jobs=5,
            handle_signals=False,
        )
        print("  [burst] worker run start")
        await w.async_run()
        print(f"  [burst] worker done  jobs_complete={w.jobs_complete} "
              f"jobs_failed={w.jobs_failed} jobs_retried={w.jobs_retried}")
        await w.close()

    _asyncio.run(_burst())

    ev_after = _query_event(settings.database_url, notification_id)
    assert ev_after is not None
    # worker 真的介入了 event（不管是 processed / failed / ignored 都算），
    # 关键是不能停留在 received。注意：伪造的 smoke-shop 和 smoke-order-1
    # 在真实 DB 里没有，worker 要么 no_shop=failed，要么尝试调 TT API 失败=failed；
    # 两者都说明 worker 取到了这条 job 并走了完整分支。
    assert ev_after[1] != "received", f"event still received, worker didn't consume: {ev_after}"
    print(f"  OK event_id={ev_after[0]} status={ev_after[1]}（worker 已介入 → 不再是 received）")

    _cleanup(settings.database_url, notification_id)
    queue_len_final = _arq_queue_length(settings.redis_url)
    print(f"  arq 队列长度 = {queue_len_final}")
    print()
    print("=" * 60)
    print("全部通过 OK — webhook 接收 + 幂等 + 验签 + arq 投递 + worker 处理 贯通")
    print("=" * 60)


if __name__ == "__main__":
    main()
