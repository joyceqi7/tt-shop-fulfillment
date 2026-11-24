"""Sentry 初始化。

SENTRY_DSN 未配置时整块是 no-op。
- FastAPI 进程启动时调 `init_sentry()` 一次
- arq worker 启动时（WorkerSettings.on_startup）也要调一次（两个独立进程）

单元测试里也会调，所以 `init_sentry` 需要幂等（Sentry SDK 自身允许多次 init，后面的覆盖前面的，ok）。
"""

from __future__ import annotations

import logging

from app.config import get_settings


logger = logging.getLogger(__name__)


_initialized = False


def init_sentry() -> bool:
    """初始化 Sentry。返回是否真的初始化（用于测试/诊断）。"""
    global _initialized
    settings = get_settings()
    if not settings.sentry_dsn:
        logger.debug("sentry DSN not configured, skip")
        return False

    try:
        import sentry_sdk
    except ImportError:
        logger.warning("sentry_sdk not installed, skip")
        return False

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        traces_sample_rate=0.05,  # 低采样，主要靠 error capture
        send_default_pii=False,
    )
    _initialized = True
    logger.info("sentry initialized env=%s", settings.app_env)
    return True


def capture_exception(exc: BaseException, **tags: str) -> None:
    """显式上报异常（不抛）。未 init 时静默。"""
    if not _initialized:
        return
    try:
        import sentry_sdk

        with sentry_sdk.push_scope() as scope:
            for k, v in tags.items():
                scope.set_tag(k, v)
            sentry_sdk.capture_exception(exc)
    except Exception:
        logger.exception("sentry capture_exception failed (swallowed)")
