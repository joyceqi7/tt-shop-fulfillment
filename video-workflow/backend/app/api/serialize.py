"""把 ORM 对象 -> dict 时顺便注入 *_url 字段（前端要播放需要 URL）。"""
from __future__ import annotations

from typing import Any

from app.api.files import to_url
from app.core import models


def source_dict(s: models.Source, latest_job: models.Job | None = None) -> dict[str, Any]:
    status = s.status
    error = s.error
    if status in ("queued", "processing") and latest_job is not None:
        if latest_job.status == "failed":
            status = "failed"
            error = error or latest_job.error
        elif latest_job.status == "done":
            status = "done"
            error = None
        elif latest_job.status == "running":
            status = "processing"
        elif latest_job.status == "queued":
            status = "queued"

    job_log = None
    if latest_job and latest_job.log:
        job_log = latest_job.log[-4000:]

    return {
        "id": s.id,
        "url": s.url,
        "platform": s.platform,
        "game": s.game,
        "original_title": s.original_title,
        "author": s.author,
        "duration": s.duration,
        "thumbnail_path": s.thumbnail_path,
        "thumbnail_url": to_url(s.thumbnail_path),
        "processed_url": to_url(s.processed_path),
        "status": status,
        "error": error,
        "job_id": latest_job.id if latest_job else None,
        "job_status": latest_job.status if latest_job else None,
        "job_progress": latest_job.progress if latest_job else None,
        "job_stage": latest_job.stage if latest_job else None,
        "job_error": latest_job.error if latest_job else None,
        "job_log": job_log,
        "job_started_at": latest_job.started_at if latest_job else None,
        "job_finished_at": latest_job.finished_at if latest_job else None,
        "created_at": s.created_at,
    }


def clip_dict(c: models.Clip) -> dict[str, Any]:
    return {
        "id": c.id,
        "source_id": c.source_id,
        "game": c.source.game if c.source else None,
        "start": c.start,
        "end": c.end,
        "duration": c.duration,
        "file_path": c.file_path,
        "file_url": to_url(c.file_path),
        "thumbnail_path": c.thumbnail_path,
        "thumbnail_url": to_url(c.thumbnail_path),
        "transcript": c.transcript,
        "description": c.description,
        "tags": c.tags,
        "content_type": c.content_type,
        "mood": c.mood,
        "hook_score": c.hook_score,
        "has_real_person": bool(c.has_real_person),
        "real_person_note": c.real_person_note,
        "used_count": c.used_count or 0,
        "created_at": c.created_at,
    }


def generation_dict(g: models.Generation) -> dict[str, Any]:
    return {
        "id": g.id,
        "game": g.game,
        "product_name": g.product_name,
        "product_info": g.product_info,
        "target_duration": g.target_duration,
        "style": g.style,
        "voice": g.voice,
        "bgm_style": g.bgm_style,
        "bgm_file": g.bgm_file,
        "bgm_category": g.bgm_category,
        "bgm_start_mode": g.bgm_start_mode,
        "subtitle_font": g.subtitle_font,
        "subtitle_highlight_font": g.subtitle_highlight_font,
        "script_json": g.script_json,
        "used_clip_ids": g.used_clip_ids,
        "output_path": g.output_path,
        "output_url": to_url(g.output_path),
        "subtitle_path": g.subtitle_path,
        "subtitle_url": to_url(g.subtitle_path),
        "voiceover_path": g.voiceover_path,
        "voiceover_url": to_url(g.voiceover_path),
        "cover_path": g.cover_path,
        "cover_url": to_url(g.cover_path),
        "status": g.status,
        "error": g.error,
        "created_at": g.created_at,
        "finished_at": g.finished_at,
    }
