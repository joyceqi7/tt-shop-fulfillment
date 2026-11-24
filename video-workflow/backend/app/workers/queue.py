"""轻量后台任务队列：用 threading 实现，限制并发数。

为什么不用 Celery / RQ?
- MVP 阶段单机跑，没必要再装 Redis
- 任务里有重 IO（ffmpeg / yt-dlp / whisper），thread 已经够用
- 失败重试 / 状态持久化都落在数据库里，重启不丢任务（启动时 reload）
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Callable

from sqlalchemy import func

from app.core.config import settings
from app.core.db import db_session
from app.core import models
from app.core.logger import get_logger

log = get_logger(__name__)


_executor: ThreadPoolExecutor | None = None
_dispatcher_thread: threading.Thread | None = None
_stop_flag = threading.Event()

_handlers: dict[str, Callable[[int, "JobContext"], None]] = {}


class JobContext:
    """传给 handler 的上下文，提供更新进度/日志的便捷方法。"""

    def __init__(self, job_id: int):
        self.job_id = job_id

    def update(
        self,
        *,
        progress: float | None = None,
        stage: str | None = None,
        log_line: str | None = None,
    ) -> None:
        with db_session() as db:
            job = db.get(models.Job, self.job_id)
            if not job:
                return
            if progress is not None:
                job.progress = max(0.0, min(100.0, progress))
            if stage is not None:
                job.stage = stage
            if log_line:
                job.log = (job.log or "") + f"[{datetime.utcnow().isoformat(timespec='seconds')}] {log_line}\n"
        if log_line:
            log.info("[job %s] %s", self.job_id, log_line)


def register(kind: str):
    """装饰器：注册某种 kind 的任务 handler。"""
    def deco(fn):
        _handlers[kind] = fn
        return fn
    return deco


def enqueue(kind: str, target_id: int) -> int:
    """新建一条 Job 并入队，返回 job_id。"""
    with db_session() as db:
        job = models.Job(kind=kind, target_id=target_id, status="queued", progress=0.0)
        db.add(job)
        db.flush()
        db.refresh(job)
        return job.id


def _dispatch_one(job_id: int) -> None:
    with db_session() as db:
        job = db.get(models.Job, job_id)
        if not job:
            return
        if job.status == "queued":
            # 兼容测试或旧代码路径直接调用 _dispatch_one 的情况。
            job.status = "running"
            job.started_at = datetime.utcnow()
        elif job.status != "running":
            return
        kind = job.kind

    ctx = JobContext(job_id)
    handler = _handlers.get(kind)
    if handler is None:
        ctx.update(log_line=f"未知任务类型: {kind}")
        _finish(job_id, status="failed", error=f"unknown kind: {kind}")
        return

    try:
        handler(job_id, ctx)
        _finish(job_id, status="done")
    except Exception as e:
        log.exception("[job %s] failed", job_id)
        ctx.update(log_line=f"FAILED: {e}")
        _finish(job_id, status="failed", error=str(e))


def _finish(job_id: int, status: str, error: str | None = None) -> None:
    with db_session() as db:
        job = db.get(models.Job, job_id)
        if not job:
            return
        job.status = status
        job.finished_at = datetime.utcnow()
        if status == "done":
            job.progress = 100.0
        if error:
            job.error = error
        if job.kind == "ingest" and job.target_id is not None:
            source = db.get(models.Source, job.target_id)
            if source:
                if status == "done":
                    source.status = "done"
                    source.error = None
                elif status == "failed" and source.status != "done":
                    source.status = "failed"
                    source.error = error or job.error or "素材处理失败"


def _dispatcher_loop() -> None:
    log.info("[queue] dispatcher started (max_concurrent=%d)", settings.MAX_CONCURRENT_JOBS)
    while not _stop_flag.is_set():
        with db_session() as db:
            running_count = int(
                db.query(func.count(models.Job.id))
                .filter(models.Job.status == "running")
                .scalar()
                or 0
            )
            free_slots = max(0, settings.MAX_CONCURRENT_JOBS - running_count)
            if free_slots <= 0:
                ids = []
            else:
                # 在提交线程池前先把任务标成 running，避免长任务排队时每秒重复提交同一个 Job。
                queued = (
                    db.query(models.Job)
                    .filter(models.Job.status == "queued")
                    .order_by(models.Job.id.asc())
                    .limit(free_slots)
                    .all()
                )
                now = datetime.utcnow()
                ids = []
                for job in queued:
                    job.status = "running"
                    job.started_at = now
                    ids.append(job.id)

        for jid in ids:
            assert _executor is not None
            try:
                _executor.submit(_dispatch_one, jid)
            except Exception as e:
                log.exception("[queue] submit job %s failed", jid)
                _finish(jid, status="failed", error=f"submit failed: {e}")

        time.sleep(1.0)
    log.info("[queue] dispatcher stopped")


def start() -> None:
    global _executor, _dispatcher_thread
    if _executor is not None:
        return
    _stop_flag.clear()
    _executor = ThreadPoolExecutor(max_workers=settings.MAX_CONCURRENT_JOBS)
    _reset_orphan_running()
    _dispatcher_thread = threading.Thread(target=_dispatcher_loop, name="job-dispatcher", daemon=True)
    _dispatcher_thread.start()


def stop() -> None:
    _stop_flag.set()
    if _executor:
        _executor.shutdown(wait=False, cancel_futures=True)


def _reset_orphan_running() -> None:
    """启动时把上次崩溃留下的 'running' 任务标为失败。"""
    with db_session() as db:
        running = db.query(models.Job).filter(models.Job.status == "running").all()
        for j in running:
            j.status = "failed"
            j.error = (j.error or "") + " (server restarted while running)"
            j.finished_at = datetime.utcnow()
            if j.kind == "ingest" and j.target_id is not None:
                source = db.get(models.Source, j.target_id)
                if source and source.status in ("queued", "processing"):
                    source.status = "failed"
                    source.error = j.error or "素材处理任务因服务重启中断，请重试。"
