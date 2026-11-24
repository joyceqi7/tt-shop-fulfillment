"""重试策略 / 死信 / 告警 smoke test。

mock fulfill_order_once 抛不同异常，验证 fulfill_order worker 的状态机：

| 场景                                 | 抛的异常                    | try/max | 期望 status | 期望告警 | 期望抛出（arq 重试）|
|--------------------------------------|----------------------------|---------|-------------|---------|--------------------|
| 1. non-retryable 业务错              | TTSApiError code=11000 200 | 1/5     | failed      | Yes     | No（return）       |
| 2. retryable + 非最后一次 attempt    | RuntimeError               | 1/5     | retrying    | No      | Yes                |
| 3. retryable + 最后一次 attempt      | RuntimeError               | 5/5     | dead        | Yes     | Yes                |
| 4. 成功                              | -                          | 1/5     | processed   | No      | No                 |

测试 alert_dead_letter 被调时附带的参数是否正确。
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg  # noqa: E402
from sqlalchemy import delete  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db.session import session_factory  # noqa: E402
from app.models.shop import TTSShop  # noqa: E402
from app.models.webhook_event import WebhookEvent  # noqa: E402
from app.services.tts_client import TTSApiError  # noqa: E402
from app.workers import tasks as tasks_mod  # noqa: E402


SHOP_ID = "dl-shop-001"
SHOP_CIPHER = "dl-cipher"


def _sync_dsn(async_dsn: str) -> str:
    return async_dsn.replace("postgresql+asyncpg://", "postgresql://")


def _status_of(dsn: str, event_id: int) -> tuple[str, str | None]:
    with psycopg.connect(_sync_dsn(dsn), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, error FROM webhook_events WHERE id = %s", (event_id,)
            )
            row = cur.fetchone()
            assert row is not None
            return row[0], row[1]


async def _prepare_shop() -> None:
    sf = session_factory()
    async with sf() as s:
        async with s.begin():
            await s.execute(delete(TTSShop).where(TTSShop.shop_id == SHOP_ID))
            s.add(
                TTSShop(
                    shop_id=SHOP_ID,
                    shop_name="DL Shop",
                    shop_cipher=SHOP_CIPHER,
                    region="US",
                )
            )


async def _create_event(order_id: str) -> int:
    sf = session_factory()
    async with sf() as s:
        ev = WebhookEvent(
            dedup_key=f"dl-{uuid.uuid4().hex[:12]}",
            event_type="ORDER_STATUS_CHANGE",
            shop_id=SHOP_ID,
            order_id=order_id,
            raw_payload={"source": "dead-letter-smoke"},
        )
        s.add(ev)
        await s.commit()
        return ev.id


class AlertRecorder:
    """替身 alert_dead_letter，记录调用参数。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


async def _run_case(
    *,
    monkey_exc: Exception | None,
    job_try: int,
    max_tries: int,
    event_id: int,
) -> tuple[bool, Any, AlertRecorder]:
    """统一 runner：monkey patch fulfill_order_once 和 alert_dead_letter，
    返回 (是否抛出, 结果/异常, 告警记录器)。"""
    recorder = AlertRecorder()

    async def fake_once(**_kw):
        if monkey_exc is not None:
            raise monkey_exc
        return {"status": "shipped", "shipped_count": 1, "skipped_count": 0, "failed_count": 0}

    orig_once = tasks_mod.fulfill_order_once
    orig_alert = tasks_mod.alert_dead_letter
    tasks_mod.fulfill_order_once = fake_once  # type: ignore[assignment]
    tasks_mod.alert_dead_letter = recorder  # type: ignore[assignment]

    ctx = {"job_try": job_try, "max_tries": max_tries}
    raised = False
    result: Any = None
    try:
        try:
            result = await tasks_mod.fulfill_order(ctx, event_id)
        except Exception as e:
            raised = True
            result = e
    finally:
        tasks_mod.fulfill_order_once = orig_once  # type: ignore[assignment]
        tasks_mod.alert_dead_letter = orig_alert  # type: ignore[assignment]
    return raised, result, recorder


async def case_1_non_retryable() -> None:
    print("=" * 60)
    print("Case 1: non-retryable (TTSApiError code=11000 http=200) → failed, 告警, 不抛")
    print("=" * 60)
    ev_id = await _create_event(f"dl-ord-{uuid.uuid4().hex[:8]}")
    exc = TTSApiError("sku not found", code=11000, http_status=200)
    raised, result, rec = await _run_case(
        monkey_exc=exc, job_try=1, max_tries=5, event_id=ev_id
    )
    status, error = _status_of(get_settings().database_url, ev_id)
    print(f"  raised={raised} result={result!r}")
    print(f"  status={status} error={error!r}")
    print(f"  alerts={len(rec.calls)}")
    assert raised is False, "non_retryable 不应抛（arq 不重试）"
    assert status == "failed"
    assert "TTSApiError" in (error or "")
    assert len(rec.calls) == 1
    assert rec.calls[0]["attempt"] == 1 and rec.calls[0]["max_tries"] == 5
    print("  OK")
    print()


async def case_2_retry_middle() -> None:
    print("=" * 60)
    print("Case 2: retryable, attempt 1/5 → retrying, 不告警, 抛（arq 重试）")
    print("=" * 60)
    ev_id = await _create_event(f"dl-ord-{uuid.uuid4().hex[:8]}")
    exc = RuntimeError("transient network failure")
    raised, result, rec = await _run_case(
        monkey_exc=exc, job_try=1, max_tries=5, event_id=ev_id
    )
    status, error = _status_of(get_settings().database_url, ev_id)
    print(f"  raised={raised} status={status}")
    assert raised is True
    assert status == "retrying"
    assert "attempt 1/5" in (error or "")
    assert len(rec.calls) == 0, "中间 attempt 不应触发告警"
    print("  OK")
    print()


async def case_3_retry_final() -> None:
    print("=" * 60)
    print("Case 3: retryable, attempt 5/5 → dead, 告警, 抛")
    print("=" * 60)
    ev_id = await _create_event(f"dl-ord-{uuid.uuid4().hex[:8]}")
    exc = RuntimeError("still failing")
    raised, result, rec = await _run_case(
        monkey_exc=exc, job_try=5, max_tries=5, event_id=ev_id
    )
    status, error = _status_of(get_settings().database_url, ev_id)
    print(f"  raised={raised} status={status}")
    print(f"  alerts={len(rec.calls)} call[0]={rec.calls[0] if rec.calls else None}")
    assert raised is True
    assert status == "dead"
    assert "attempt 5/5" in (error or "")
    assert len(rec.calls) == 1
    c = rec.calls[0]
    assert c["event_id"] == ev_id
    assert c["attempt"] == 5 and c["max_tries"] == 5
    print("  OK")
    print()


async def case_4_success() -> None:
    print("=" * 60)
    print("Case 4: 正常成功 → processed, 不告警, 不抛")
    print("=" * 60)
    ev_id = await _create_event(f"dl-ord-{uuid.uuid4().hex[:8]}")
    raised, result, rec = await _run_case(
        monkey_exc=None, job_try=1, max_tries=5, event_id=ev_id
    )
    status, _ = _status_of(get_settings().database_url, ev_id)
    print(f"  raised={raised} status={status} result={result!r}")
    assert raised is False
    assert status == "processed"
    assert len(rec.calls) == 0
    print("  OK")
    print()


async def _cleanup() -> None:
    sf = session_factory()
    async with sf() as s:
        async with s.begin():
            await s.execute(
                delete(WebhookEvent).where(WebhookEvent.shop_id == SHOP_ID)
            )
            await s.execute(delete(TTSShop).where(TTSShop.shop_id == SHOP_ID))


async def main() -> None:
    await _prepare_shop()
    try:
        await case_1_non_retryable()
        await case_2_retry_middle()
        await case_3_retry_final()
        await case_4_success()
    finally:
        await _cleanup()

    print("=" * 60)
    print("全部通过 OK — 错误分类 + retrying / dead / failed / processed 状态机 + 告警阈值")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
