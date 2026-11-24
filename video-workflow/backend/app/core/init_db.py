"""初始化数据库（建表 + 轻量迁移）。

用法:
    python -m app.core.init_db
"""
from datetime import datetime, timedelta

from sqlalchemy import inspect, text

from app.core.db import engine, Base
from app.core import models  # noqa: F401  确保模型被注册
from app.core.config import settings
from app.core.db import db_session


# (table, column, sql_type) — 启动时检测, 缺失就 ALTER TABLE 加上
_LIGHTWEIGHT_MIGRATIONS = [
    ("sources", "game", "VARCHAR(64)"),
    ("sources", "crop_top", "INTEGER DEFAULT 0"),
    ("sources", "crop_bottom", "INTEGER DEFAULT 0"),
    ("generations", "game", "VARCHAR(64)"),
    ("clips", "has_real_person", "BOOLEAN DEFAULT 0"),
    ("clips", "real_person_note", "VARCHAR(256)"),
    ("generations", "bgm_style", "VARCHAR(32)"),
    ("generations", "bgm_file", "VARCHAR(512)"),
    ("generations", "bgm_category", "VARCHAR(64)"),
    ("generations", "bgm_start_mode", "VARCHAR(32) DEFAULT 'smart'"),
    ("generations", "subtitle_font", "VARCHAR(64)"),
    ("generations", "subtitle_highlight_font", "VARCHAR(64)"),
    ("generations", "cover_path", "VARCHAR(512)"),
]


def apply_lightweight_migrations() -> None:
    """SQLite 支持 ALTER TABLE ADD COLUMN, 用来加新字段不丢老数据."""
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, column, sqltype in _LIGHTWEIGHT_MIGRATIONS:
            if not insp.has_table(table):
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            if column in existing:
                continue
            print(f"[migrate] add {table}.{column} ({sqltype})")
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sqltype}"))


def repair_stale_source_statuses() -> None:
    """把历史遗留的 Source 状态和最新 ingest Job 对齐。

    之前 ingest 任务失败时只标记 Job.failed, Source 仍可能停在 processing。
    页面按 Source.status 展示, 这会让运营看到"一直处理中"。启动时修一次老数据,
    后续失败则由任务代码实时写回。
    """
    cutoff = datetime.utcnow() - timedelta(minutes=10)
    with db_session() as db:
        sources = (
            db.query(models.Source)
            .filter(models.Source.status.in_(("queued", "processing")))
            .all()
        )
        for source in sources:
            latest_job = (
                db.query(models.Job)
                .filter(models.Job.kind == "ingest", models.Job.target_id == source.id)
                .order_by(models.Job.id.desc())
                .first()
            )
            if latest_job is None:
                if source.created_at and source.created_at < cutoff:
                    source.status = "failed"
                    source.error = "没有找到对应的素材处理任务，请重新提交素材。"
                continue
            if latest_job.status == "failed":
                source.status = "failed"
                source.error = source.error or latest_job.error or "素材处理失败"
            elif latest_job.status == "done":
                source.status = "done"
                source.error = None


def main():
    settings.storage_path
    Base.metadata.create_all(bind=engine)
    apply_lightweight_migrations()
    repair_stale_source_statuses()
    print(f"[ok] Database initialized at: {settings.DATABASE_URL}")
    print(f"[ok] Storage root: {settings.storage_path}")


if __name__ == "__main__":
    main()
