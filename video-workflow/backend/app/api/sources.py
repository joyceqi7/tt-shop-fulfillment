import os
import re
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core import models
from app.core.config import settings
from app.core.db import get_db
from app.core.logger import get_logger
from app.api.schemas import IngestRequest, SourceOut, SourceUpdate, ClipOut
from app.api.serialize import source_dict, clip_dict
from app.workers import queue

log = get_logger(__name__)

router = APIRouter(prefix="/sources", tags=["sources"])

ALLOWED_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".flv", ".ts"}
MAX_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024  # 4 GB — Caddy 同步放到 4GB


def _safe_filename(name: str) -> str:
    name = re.sub(r"[^\w.\-]+", "_", name).strip("._") or "file"
    return name[:120]


@router.post("/ingest", response_model=list[SourceOut])
def ingest(req: IngestRequest, db: Session = Depends(get_db)):
    created: list[models.Source] = []
    game = (req.game or "").strip() or None
    for raw_url in req.urls:
        url = raw_url.strip()
        if not url:
            continue
        src = models.Source(
            url=url,
            status="queued",
            game=game,
            crop_top=max(0, int(req.crop_top or 0)),
            crop_bottom=max(0, int(req.crop_bottom or 0)),
        )
        db.add(src)
        created.append(src)
    db.commit()
    for s in created:
        db.refresh(s)
        queue.enqueue("ingest", s.id)
    return [source_dict(s, _latest_ingest_job(db, s.id)) for s in created]


@router.post("/upload", response_model=list[SourceOut])
async def upload(
    files: list[UploadFile] = File(..., description="一次可上传多个视频文件"),
    game: str = Form(default=""),
    crop_top: int = Form(default=0),
    crop_bottom: int = Form(default=0),
    db: Session = Depends(get_db),
):
    """上传本地视频文件作为素材源。

    每个文件保存到 storage/raw/ 后立刻入队 ingest 任务，
    任务里检测到 raw_path 已存在就跳过下载步骤，直接走去水印 / ASR / 切镜 / 打标。

    出错时务必返回结构化 4xx, 上层 Caddy + Next.js 路径里任何一个 500 都意味着是
    后端真异常 —— 启动时和首批请求时把日志打全, 排障靠日志而不是 0 信息的 500。
    """
    if not files:
        raise HTTPException(400, "no files")

    # 提前确认 storage 可写, 把"挂载点没建好 / 只读 / 权限不对"暴露成清晰错误而不是 500
    try:
        raw_dir = settings.storage_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        if not os.access(raw_dir, os.W_OK):
            raise PermissionError(f"raw_dir not writable: {raw_dir}")
    except Exception as e:
        log.exception("[upload] storage_path 准备失败")
        raise HTTPException(
            500,
            f"存储目录不可用: {e}. 请检查容器是否绑定了 /data/workflow 并具备写权限.",
        )

    game_value = (game or "").strip() or None
    crop_top_value = max(0, int(crop_top or 0))
    crop_bottom_value = max(0, int(crop_bottom or 0))

    created: list[models.Source] = []
    errors: list[str] = []
    total_received = 0

    for upload_file in files:
        orig_name = upload_file.filename or "upload"
        ext = Path(orig_name).suffix.lower()
        if ext not in ALLOWED_VIDEO_EXTS:
            errors.append(f"{orig_name}: 不支持的视频格式 {ext or '(no ext)'}")
            continue

        stem = _safe_filename(Path(orig_name).stem)
        target = raw_dir / f"{stem}_{int(time.time()*1000)}{ext}"

        size_total = 0
        too_large = False
        write_failed: str | None = None
        try:
            with target.open("wb") as f:
                while True:
                    try:
                        chunk = await upload_file.read(1024 * 1024)
                    except Exception as e:
                        write_failed = f"读取上传流失败: {e}"
                        break
                    if not chunk:
                        break
                    size_total += len(chunk)
                    if size_total > MAX_UPLOAD_BYTES:
                        too_large = True
                        break
                    try:
                        f.write(chunk)
                    except OSError as e:
                        write_failed = f"写入磁盘失败: {e}"
                        break
        except Exception as e:
            log.exception("[upload] 写入 %s 出现意外异常", target)
            write_failed = f"未知异常: {e}"
        finally:
            try:
                await upload_file.close()
            except Exception:
                pass

        if write_failed:
            target.unlink(missing_ok=True)
            errors.append(f"{orig_name}: {write_failed}")
            continue
        if too_large:
            target.unlink(missing_ok=True)
            errors.append(f"{orig_name}: 超过 {MAX_UPLOAD_BYTES // (1024**3)}GB 上限")
            continue
        if not target.exists() or size_total == 0:
            errors.append(f"{orig_name}: 上传失败 (空文件 0 字节)")
            target.unlink(missing_ok=True)
            continue

        try:
            src = models.Source(
                url=f"local://{target.name}",
                platform="local",
                game=game_value,
                original_title=orig_name[:512],
                raw_path=str(target),
                status="queued",
                crop_top=crop_top_value,
                crop_bottom=crop_bottom_value,
            )
            db.add(src)
            created.append(src)
            total_received += size_total
        except Exception as e:
            log.exception("[upload] 建库失败 %s", orig_name)
            target.unlink(missing_ok=True)
            errors.append(f"{orig_name}: 数据库写入失败: {e}")
            continue

    try:
        db.commit()
    except Exception as e:
        log.exception("[upload] db.commit 失败")
        db.rollback()
        # 落地的临时文件清掉, 避免脏数据
        for s in created:
            if s.raw_path:
                Path(s.raw_path).unlink(missing_ok=True)
        raise HTTPException(500, f"数据库提交失败: {e}")

    for s in created:
        db.refresh(s)
        try:
            queue.enqueue("ingest", s.id)
        except Exception:
            log.exception("[upload] enqueue ingest 失败 source=%s", s.id)
            # 入队失败不阻塞上传成功响应, 用户可以稍后在任务页手动重试

    log.info(
        "[upload] 成功 %d 个 / 共收 %.1f MB / 错误 %d 个",
        len(created),
        total_received / (1024 * 1024),
        len(errors),
    )

    # 全部失败 → 400 + 完整原因, 不要给前端一个 200 [] 让人以为成功
    if errors and not created:
        raise HTTPException(400, "; ".join(errors))

    response = [source_dict(s, _latest_ingest_job(db, s.id)) for s in created]
    # 部分失败时把 warning 放到 header, 前端可选择性展示
    if errors:
        log.warning("[upload] 部分失败: %s", "; ".join(errors))
    return response


@router.get("", response_model=list[SourceOut])
def list_sources(
    db: Session = Depends(get_db),
    game: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    q = db.query(models.Source)
    if game:
        q = q.filter(models.Source.game == game)
    rows = q.order_by(models.Source.id.desc()).offset(offset).limit(limit).all()
    latest_jobs = _latest_ingest_jobs(db, [s.id for s in rows])
    return [source_dict(s, latest_jobs.get(s.id)) for s in rows]


@router.get("/{source_id}", response_model=SourceOut)
def get_source(source_id: int, db: Session = Depends(get_db)):
    s = db.get(models.Source, source_id)
    if not s:
        raise HTTPException(404, "source not found")
    return source_dict(s, _latest_ingest_job(db, s.id))


@router.patch("/{source_id}", response_model=SourceOut)
def update_source(source_id: int, patch: SourceUpdate, db: Session = Depends(get_db)):
    s = db.get(models.Source, source_id)
    if not s:
        raise HTTPException(404, "source not found")
    if patch.game is not None:
        s.game = (patch.game or "").strip() or None
    db.commit()
    db.refresh(s)
    return source_dict(s, _latest_ingest_job(db, s.id))


@router.post("/{source_id}/retry", response_model=SourceOut)
def retry_source(source_id: int, db: Session = Depends(get_db)):
    s = db.get(models.Source, source_id)
    if not s:
        raise HTTPException(404, "source not found")
    latest_job = _latest_ingest_job(db, s.id)
    if latest_job and latest_job.status in ("queued", "running"):
        raise HTTPException(409, "source already has an active ingest job")

    # 失败后重试时清掉可能残留的片段记录，避免重跑后素材库出现重复片段。
    for clip in list(s.clips):
        db.delete(clip)
    s.status = "queued"
    s.error = None
    s.transcript = None
    s.transcript_segments = None
    db.commit()
    db.refresh(s)
    queue.enqueue("ingest", s.id)
    db.refresh(s)
    return source_dict(s, _latest_ingest_job(db, s.id))


@router.get("/{source_id}/clips", response_model=list[ClipOut])
def list_clips_for_source(source_id: int, db: Session = Depends(get_db)):
    rows = (
        db.query(models.Clip)
        .filter(models.Clip.source_id == source_id)
        .order_by(models.Clip.start.asc())
        .all()
    )
    return [clip_dict(c) for c in rows]


@router.delete("/{source_id}")
def delete_source(source_id: int, db: Session = Depends(get_db)):
    from pathlib import Path

    s = db.get(models.Source, source_id)
    if not s:
        raise HTTPException(404, "source not found")

    files_to_remove: list[str] = []
    for c in s.clips:
        files_to_remove += [c.file_path, c.thumbnail_path]
    files_to_remove += [s.raw_path, s.processed_path, s.thumbnail_path]

    db.delete(s)
    db.commit()

    for f in files_to_remove:
        if not f:
            continue
        try:
            Path(f).unlink(missing_ok=True)
        except Exception:
            pass
    return {"ok": True}


def _latest_ingest_job(db: Session, source_id: int) -> models.Job | None:
    return (
        db.query(models.Job)
        .filter(models.Job.kind == "ingest", models.Job.target_id == source_id)
        .order_by(models.Job.id.desc())
        .first()
    )


def _latest_ingest_jobs(db: Session, source_ids: list[int]) -> dict[int, models.Job]:
    if not source_ids:
        return {}
    rows = (
        db.query(models.Job)
        .filter(models.Job.kind == "ingest", models.Job.target_id.in_(source_ids))
        .order_by(models.Job.target_id.asc(), models.Job.id.desc())
        .all()
    )
    latest: dict[int, models.Job] = {}
    for job in rows:
        if job.target_id is None or job.target_id in latest:
            continue
        latest[int(job.target_id)] = job
    return latest
