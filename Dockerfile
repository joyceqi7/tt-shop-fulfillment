# ===========================================
# TT Shop 履约系统 —— FastAPI + arq worker 共用镜像
#
# 一个镜像两种用法：
#   docker run ... tt-shop  uvicorn app.api.main:app --host 0.0.0.0 --port 8000
#   docker run ... tt-shop  arq app.workers.settings.WorkerSettings
# 因为 api 和 worker 共享一份代码、依赖、配置，分镜像意义不大。
# ===========================================

FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 编译 asyncpg / cryptography 需要 build-essential
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip install --prefix=/install -r requirements.txt


FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=UTC

# 运行时只要 libpq5（asyncpg 实际用 protocol 直连，但 pg_dump 客户端工具留着方便排错）
# 用发行版默认的 postgresql-client，避免 Debian 升级到 bookworm/trixie 后 -15 不存在
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    postgresql-client \
    curl \
    ca-certificates \
    tini \
    && rm -rf /var/lib/apt/lists/*

# 非 root 用户跑应用，符合容器最佳实践
RUN useradd --system --create-home --uid 1000 app

COPY --from=builder /install /usr/local
WORKDIR /app
COPY --chown=app:app . .

USER app

# 健康检查 —— FastAPI /health/live；arq worker 容器层面用 docker compose 的 healthcheck 覆盖
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health/live || exit 1

EXPOSE 8000

# tini 处理 PID 1 的信号转发，避免 docker stop 时 graceful shutdown 失败
ENTRYPOINT ["tini", "--"]

# 默认起 FastAPI；compose 里给 worker 服务覆盖成 arq 命令
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
