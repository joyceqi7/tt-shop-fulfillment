"""命令行工具：直接跑 pipeline 单步，方便调试 / 批量。

用法:
    python -m app.cli ingest "https://www.douyin.com/video/xxx"
    python -m app.cli ingest urls.txt   # 一行一个 URL
    python -m app.cli generate "Call of Duty MW3 CDKey" --info "80% off" --duration 30
    python -m app.cli stats
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from app.core.db import db_session, engine, Base
from app.core import models  # noqa: F401
from app.core.logger import setup_logging, get_logger
from app.workers import queue
from app.workers import tasks  # noqa: F401

log = get_logger("cli")


def _wait_for_jobs():
    while True:
        with db_session() as db:
            pending = db.query(models.Job).filter(
                models.Job.status.in_(["queued", "running"])
            ).count()
        if pending == 0:
            return
        time.sleep(2)


def cmd_ingest(args):
    Base.metadata.create_all(bind=engine)
    queue.start()

    items: list[str] = []
    for arg in args.urls:
        p = Path(arg)
        if p.is_file():
            items += [l.strip() for l in p.read_text().splitlines() if l.strip().startswith("http")]
        elif arg.startswith("http"):
            items.append(arg)
        else:
            log.warning("ignored: %s", arg)

    if not items:
        log.error("no urls"); sys.exit(2)

    ids = []
    with db_session() as db:
        for url in items:
            src = models.Source(url=url, status="queued")
            db.add(src); db.flush()
            queue.enqueue("ingest", src.id)
            ids.append(src.id)
    log.info("enqueued ingest for %d sources: %s", len(ids), ids)
    _wait_for_jobs()
    log.info("all ingest jobs finished")


def cmd_generate(args):
    Base.metadata.create_all(bind=engine)
    queue.start()
    with db_session() as db:
        gen = models.Generation(
            product_name=args.product,
            product_info=args.info or "",
            target_duration=args.duration,
            style=args.style,
            voice=args.voice,
            status="queued",
        )
        db.add(gen); db.flush()
        queue.enqueue("generate", gen.id)
        gid = gen.id
    log.info("enqueued generation #%d", gid)
    _wait_for_jobs()
    with db_session() as db:
        g = db.get(models.Generation, gid)
        if g.status == "done":
            log.info("DONE -> %s", g.output_path)
        else:
            log.error("FAILED: %s", g.error)


def cmd_stats(_args):
    with db_session() as db:
        print(f"sources       = {db.query(models.Source).count()}")
        print(f"  done        = {db.query(models.Source).filter(models.Source.status=='done').count()}")
        print(f"clips         = {db.query(models.Clip).count()}")
        print(f"generations   = {db.query(models.Generation).count()}")
        print(f"jobs queued   = {db.query(models.Job).filter(models.Job.status=='queued').count()}")
        print(f"jobs running  = {db.query(models.Job).filter(models.Job.status=='running').count()}")


def main():
    setup_logging()
    parser = argparse.ArgumentParser("video-workflow-cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_in = sub.add_parser("ingest", help="下载 + 处理一批 URL")
    p_in.add_argument("urls", nargs="+", help="URL 或者 .txt 文件路径")
    p_in.set_defaults(fn=cmd_ingest)

    p_gen = sub.add_parser("generate", help="生成一条英文成片")
    p_gen.add_argument("product", help="产品名")
    p_gen.add_argument("--info", default="", help="产品卖点")
    p_gen.add_argument("--duration", type=int, default=30)
    p_gen.add_argument("--style", default="energetic")
    p_gen.add_argument("--voice", default=None)
    p_gen.set_defaults(fn=cmd_generate)

    p_st = sub.add_parser("stats", help="看一下当前库存")
    p_st.set_defaults(fn=cmd_stats)

    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
