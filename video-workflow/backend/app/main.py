"""FastAPI 入口。启动时:
  1) 初始化日志
  2) 确保 storage 目录存在
  3) 注册任务 handler（通过 import 触发装饰器）
  4) 启动后台任务调度
  5) 注册 REST 路由
"""
import time
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.db import engine, Base
from app.core import models  # noqa: F401
from app.core.logger import setup_logging, get_logger
from app.workers import queue
from app.workers import tasks  # noqa: F401  注册 handler
from app.api import sources, clips, jobs, generate, stats, files, games, bgm, fonts

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    # 启动时确保 storage 可写, 提前暴露挂载问题
    try:
        sp = settings.storage_path
        log.info("storage ready at %s", sp)
    except Exception:
        log.exception("storage 初始化失败, 后续上传会报错")
    Base.metadata.create_all(bind=engine)
    from app.core.init_db import apply_lightweight_migrations, repair_stale_source_statuses
    apply_lightweight_migrations()
    repair_stale_source_statuses()
    queue.start()
    log.info("workflow-api ready on :8001")
    log.info("docs at /docs")
    yield
    queue.stop()


app = FastAPI(title="Video Workflow", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def access_log_and_safe_500(request: Request, call_next):
    """请求日志 + 兜底异常处理。

    1) 上线后排查上传 500 时, 默认 uvicorn 的 INFO 行只打 method/path/status, 没有耗时,
       看不到是 client 断了还是后端处理慢. 这里加 elapsed_ms.
    2) FastAPI 默认未捕获异常会直接 raise 出去, Starlette 兜底返回空体 500.
       这一层把 traceback 截短回前端, 部署排错时不用再 docker logs 反复 grep.
    """
    started = time.perf_counter()
    method = request.method
    path = request.url.path
    try:
        response = await call_next(request)
    except Exception as exc:  # noqa: BLE001
        elapsed = (time.perf_counter() - started) * 1000
        tb = traceback.format_exc()
        log.error("[%s %s] unhandled exception after %.0fms\n%s", method, path, elapsed, tb)
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_server_error",
                "detail": str(exc),
                # 只回最后几帧, 安全 + 够定位
                "trace_tail": tb.strip().splitlines()[-6:],
            },
        )
    elapsed = (time.perf_counter() - started) * 1000
    # 慢请求或非 2xx 打 WARN, 其余 DEBUG 太吵不打
    if response.status_code >= 400 or elapsed > 2000:
        log.warning("[%s %s] -> %d in %.0fms", method, path, response.status_code, elapsed)
    return response


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(request: Request, exc: RequestValidationError):
    """Pydantic 校验失败时给个清晰的 400, 不要让前端拿到 422 一脸懵."""
    log.warning("[%s %s] validation failed: %s", request.method, request.url.path, exc.errors())
    return JSONResponse(status_code=400, content={"error": "validation_error", "detail": exc.errors()})


app.include_router(sources.router)
app.include_router(clips.router)
app.include_router(jobs.router)
app.include_router(generate.router)
app.include_router(stats.router)
app.include_router(files.router)
app.include_router(games.router)
app.include_router(bgm.router)
app.include_router(fonts.router)


@app.get("/")
def root():
    return {
        "name": "Video Workflow API",
        "docs": "/docs",
        "endpoints": [
            "POST /sources/ingest",
            "POST /sources/upload",
            "GET  /sources",
            "GET  /clips",
            "GET  /jobs",
            "POST /generations",
            "GET  /generations",
            "GET  /stats",
        ],
    }


@app.get("/healthz")
def healthz():
    """轻量健康检查 (docker healthcheck 用)。/stats 会查库, 上传压力大时可能慢。"""
    return {"ok": True}
