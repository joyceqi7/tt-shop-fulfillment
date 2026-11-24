"""arq Worker 配置入口。

启动方式：
    arq app.workers.settings.WorkerSettings

cron_jobs 需要 worker 持续运行才会触发——开发机上用 `arq ... --watch` 即可。
"""

from __future__ import annotations

from arq.cron import cron

from app.observability import init_sentry
from app.workers.redis_settings import build_redis_settings
from app.workers.tasks import (
    fulfill_order,
    manual_reconciliation_task,
    reconciliation_task,
    watchdog_task,
)


async def _on_startup(ctx: dict) -> None:
    init_sentry()


class WorkerSettings:
    functions = [
        fulfill_order,
        reconciliation_task,
        manual_reconciliation_task,
        watchdog_task,
    ]
    redis_settings = build_redis_settings()
    max_jobs = 10
    job_timeout = 600
    max_tries = 5
    keep_result = 60 * 60
    on_startup = _on_startup

    cron_jobs = [
        # 兜底对账：每 10 分钟
        cron(
            reconciliation_task,
            minute={0, 10, 20, 30, 40, 50},
            run_at_startup=False,
        ),
        # 健康巡检：每 5 分钟（错开对账整点偏移 2/7/12/...）
        cron(
            watchdog_task,
            minute={2, 7, 12, 17, 22, 27, 32, 37, 42, 47, 52, 57},
            run_at_startup=False,
        ),
    ]
