"""注册后台任务: ingest / generate_script / render_video / generate_cover 等.

ingest:    一个 Source 走完 下载 → 去水印 → ASR → 切镜头 → 打标 → 入库
generate:  一次 Generation 走完 选片 → 脚本 → TTS → 合成
"""
from __future__ import annotations

import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from app.core.config import settings
from app.core.db import db_session
from app.core import models
from app.core.logger import get_logger
from app.pipeline import (
    asr as asr_mod,
    compose as compose_mod,
    download as download_mod,
    scenes as scenes_mod,
    script as script_mod,
    tagging as tagging_mod,
    watermark as watermark_mod,
)
from app.pipeline.compose import ffprobe_duration as _ffprobe_duration
from app.workers.queue import register, JobContext

log = get_logger(__name__)


_DEFAULT_TAGS = {
    "description": "",
    "tags": ["unclassified"],
    "content_type": "filler",
    "mood": "neutral",
    "hook_score": 0.2,
    "has_real_person": False,
    "real_person_note": None,
}


def _safe_tag_result(raw: dict | None, transcript: str) -> dict:
    """无论 LLM 返回什么格式, 保证有完整字段, 避免 KeyError 阻塞流水线."""
    out = dict(_DEFAULT_TAGS)
    out["description"] = (transcript or "")[:80].strip() or "untitled shot"
    if isinstance(raw, dict):
        for k in out.keys():
            v = raw.get(k)
            if v is None:
                continue
            if k == "tags":
                if isinstance(v, list):
                    out[k] = [str(t).strip().lower() for t in v if str(t).strip()][:8] or out[k]
                elif isinstance(v, str):
                    parts = [t.strip().lower() for t in v.split(",") if t.strip()]
                    out[k] = parts[:8] or out[k]
            elif k == "hook_score":
                try:
                    out[k] = max(0.0, min(1.0, float(v)))
                except (TypeError, ValueError):
                    pass
            elif k == "has_real_person":
                if isinstance(v, bool):
                    out[k] = v
                elif isinstance(v, (int, float)):
                    out[k] = bool(v)
                elif isinstance(v, str):
                    s = v.strip().lower()
                    if s in {"true", "yes", "1", "y"}:
                        out[k] = True
                    elif s in {"false", "no", "0", "n"}:
                        out[k] = False
            elif k == "real_person_note":
                s = str(v).strip()[:256]
                out[k] = s if s else None
            else:
                out[k] = str(v).strip()[:300] if k == "description" else str(v).strip().lower()
    return out


@register("ingest")
def run_ingest(job_id: int, ctx: JobContext) -> None:
    source_id = _source_id_for_job(job_id)
    try:
        _run_ingest_inner(job_id, ctx)
    except Exception as e:
        _mark_source_failed(source_id, e)
        raise


def _source_id_for_job(job_id: int) -> int | None:
    try:
        with db_session() as db:
            job = db.get(models.Job, job_id)
            if not job or job.target_id is None:
                return None
            return int(job.target_id)
    except Exception:
        return None


def _mark_source_failed(source_id: int | None, exc: Exception) -> None:
    if source_id is None:
        return
    try:
        with db_session() as db:
            source = db.get(models.Source, source_id)
            if source and source.status != "done":
                source.status = "failed"
                source.error = str(exc)[:1000]
    except Exception:
        log.exception("[task] 标记 source %s 为 failed 时失败", source_id)


def _run_ingest_inner(job_id: int, ctx: JobContext) -> None:
    with db_session() as db:
        job = db.get(models.Job, job_id)
        source_id = job.target_id
        source = db.get(models.Source, source_id)
        if not source:
            raise RuntimeError(f"Source {source_id} not found")
        url = source.url
        existing_raw = source.raw_path
        existing_title = source.original_title
        crop_top = int(source.crop_top or 0)
        crop_bottom = int(source.crop_bottom or 0)
        source.status = "processing"
        source.error = None

    storage = settings.storage_path

    if existing_raw and Path(existing_raw).exists():
        ctx.update(stage="local", progress=5, log_line=f"使用本地文件: {Path(existing_raw).name}")
        raw_path = Path(existing_raw)
        try:
            width, height = watermark_mod.probe_dimensions(raw_path)
        except Exception:
            width, height = 0, 0
        try:
            duration_sec = _ffprobe_duration(raw_path)
        except Exception:
            duration_sec = 0.0
        meta_title = existing_title or raw_path.stem
        meta_author = ""
        meta_platform = "local"
    else:
        ctx.update(stage="download", progress=2, log_line=f"开始下载 {url}")
        last_download_progress = {"value": 0.0}

        def on_download_progress(percent: float, message: str) -> None:
            percent = max(0.0, min(100.0, percent))
            if percent < last_download_progress["value"] + 5 and percent < 100:
                return
            last_download_progress["value"] = percent
            ctx.update(stage="download", progress=2 + percent * 0.12, log_line=message)

        dl = download_mod.download(url, target_dir=storage / "raw", progress_cb=on_download_progress)
        ctx.update(stage="download", progress=15, log_line=f"下载完成: {dl.file_path.name} ({dl.duration:.1f}s)")
        raw_path = dl.file_path
        width, height = dl.width, dl.height
        duration_sec = dl.duration
        meta_title = dl.title
        meta_author = dl.author
        meta_platform = dl.platform

    processed_path = storage / "processed" / f"{raw_path.stem}_clean.mp4"
    watermark_mod.remove_watermark_and_audio(
        raw_path,
        processed_path,
        crop_top=crop_top,
        crop_bottom=crop_bottom,
    )
    ctx.update(stage="watermark", progress=30, log_line=f"去水印去音轨完成: {processed_path.name}")

    thumb_path = storage / "thumbnails" / f"{raw_path.stem}.jpg"
    try:
        watermark_mod.make_thumbnail(processed_path, thumb_path)
    except Exception as e:
        log.warning("缩略图生成失败: %s", e)
        thumb_path = None

    full_text, segments = asr_mod.transcribe(raw_path)
    seg_json = asr_mod.segments_to_jsonable(segments)
    ctx.update(stage="asr", progress=55, log_line=f"ASR 完成: {len(segments)} 段 / {len(full_text)} 字")

    with db_session() as db:
        source = db.get(models.Source, source_id)
        source.platform = meta_platform
        source.original_title = meta_title
        source.author = meta_author
        source.duration = duration_sec
        source.width = width
        source.height = height
        source.raw_path = str(raw_path)
        source.processed_path = str(processed_path)
        source.thumbnail_path = str(thumb_path) if thumb_path else None
        source.transcript = full_text
        source.transcript_segments = seg_json
        source.language = settings.ASR_LANGUAGE

    scene_list = scenes_mod.detect_scenes(processed_path)
    ctx.update(stage="scenes", progress=65, log_line=f"检测到 {len(scene_list)} 个镜头")

    total = max(len(scene_list), 1)
    ok_count = 0
    fail_count = 0
    for i, sc in enumerate(scene_list):
        try:
            clip_path = storage / "clips" / f"src{source_id:05d}_clip{i:03d}.mp4"
            scenes_mod.extract_clip(processed_path, clip_path, sc.start, sc.end, re_encode=True)

            frames_dir = Path(tempfile.mkdtemp(prefix=f"frames_{source_id}_{i}_"))
            try:
                frames = scenes_mod.extract_frames(clip_path, frames_dir, interval=settings.FRAME_SAMPLE_INTERVAL, max_frames=4)
                clip_transcript = asr_mod.transcript_in_range(seg_json, sc.start, sc.end)
                tag_result = tagging_mod.tag_clip(frames, transcript=clip_transcript)
            finally:
                shutil.rmtree(frames_dir, ignore_errors=True)

            tag_result = _safe_tag_result(tag_result, clip_transcript)

            clip_thumb = storage / "thumbnails" / f"src{source_id:05d}_clip{i:03d}.jpg"
            try:
                watermark_mod.make_thumbnail(clip_path, clip_thumb, time_sec=min(0.5, sc.duration / 2))
            except Exception:
                clip_thumb = None

            with db_session() as db:
                clip = models.Clip(
                    source_id=source_id,
                    start=sc.start, end=sc.end, duration=sc.duration,
                    file_path=str(clip_path),
                    thumbnail_path=str(clip_thumb) if clip_thumb else None,
                    transcript=clip_transcript,
                    description=tag_result["description"],
                    tags=tag_result["tags"],
                    content_type=tag_result["content_type"],
                    mood=tag_result["mood"],
                    hook_score=tag_result["hook_score"],
                    has_real_person=bool(tag_result.get("has_real_person", False)),
                    real_person_note=tag_result.get("real_person_note"),
                )
                db.add(clip)

            ok_count += 1
            prog = 65 + int(30 * (i + 1) / total)
            real_person_flag = " ⚠真人" if tag_result.get("has_real_person") else ""
            ctx.update(stage="tag", progress=prog,
                       log_line=f"片段 {i+1}/{total} 已入库 [{tag_result['content_type']}/{tag_result['mood']}]{real_person_flag}")
        except Exception as e:
            fail_count += 1
            log.exception("[clip %d/%d] 处理失败, 跳过", i + 1, total)
            ctx.update(log_line=f"片段 {i+1}/{total} 处理失败, 跳过: {e}")

    ctx.update(log_line=f"片段汇总: 入库 {ok_count} 个, 失败 {fail_count} 个")
    if ok_count == 0:
        raise RuntimeError("没有成功入库任何素材片段，请检查视频文件、裁剪参数或任务日志。")

    with db_session() as db:
        source = db.get(models.Source, source_id)
        source.status = "done"
        source.error = None

    ctx.update(stage="done", progress=100, log_line="入库完成")


def _gen_id_for_job(job_id: int) -> int | None:
    """先从 Job 行里取出关联 Generation.id, 与 inner() 的成败解耦。

    历史上 gen_id 是从 inner 的返回值拿的, 一旦 inner 抛异常 (例如打 LLM 时崩溃),
    外层就拿不到 gen_id, 没法把 Generation.status 置 failed, 会永久卡 scripting/rendering。
    """
    try:
        with db_session() as db:
            job = db.get(models.Job, job_id)
            if not job or job.target_id is None:
                return None
            return int(job.target_id)
    except Exception:
        return None


def _mark_generation_failed(gen_id: int | None, exc: Exception) -> None:
    if gen_id is None:
        return
    try:
        with db_session() as db:
            gen = db.get(models.Generation, gen_id)
            if gen:
                gen.status = "failed"
                gen.error = str(exc)[:1000]
                gen.finished_at = datetime.utcnow()
    except Exception:
        log.exception("[task] 标记 generation %s 为 failed 时失败", gen_id)


@register("generate_script")
def run_generate_script(job_id: int, ctx: JobContext) -> None:
    """阶段 1: 选片 + 生成脚本, 完成后状态 = awaiting_review, 等用户审核."""
    gen_id = _gen_id_for_job(job_id)
    try:
        _run_generate_script_inner(job_id, ctx)
    except Exception as e:
        _mark_generation_failed(gen_id, e)
        raise


def _run_generate_script_inner(job_id: int, ctx: JobContext) -> int:
    with db_session() as db:
        job = db.get(models.Job, job_id)
        gen_id = job.target_id
        gen = db.get(models.Generation, gen_id)
        if not gen:
            raise RuntimeError(f"Generation {gen_id} not found")
        if job.status != "running":
            ctx.update(log_line="任务已被新的请求取代，跳过")
            return gen_id
        if gen.status != "queued":
            ctx.update(log_line=f"当前生成任务状态为 {gen.status}，跳过旧脚本任务")
            return gen_id
        product_name = gen.product_name
        product_info = gen.product_info or ""
        target_duration = gen.target_duration
        style = gen.style or "energetic"
        game_filter = gen.game
        gen.status = "scripting"
        gen.error = None

    label = f"{product_name}" + (f" [{game_filter}]" if game_filter else "")
    ctx.update(stage="select", progress=10, log_line=f"开始生成脚本: {label}")

    import random
    with db_session() as db:
        q = db.query(models.Clip)
        if game_filter:
            q = q.join(models.Source).filter(models.Source.game == game_filter)
        # 肖像权安全: 默认不让真人片段进入候选池
        q = q.filter((models.Clip.has_real_person == False) | (models.Clip.has_real_person.is_(None)))  # noqa: E712
        # session 内立即把字段抽成 dict (避免出 with 后 lazy load 报 "not bound to Session")
        pool = [
            {
                "id": c.id,
                "duration": c.duration,
                "description": c.description,
                "tags": c.tags or [],
                "content_type": c.content_type,
                "mood": c.mood,
                "hook_score": c.hook_score or 0.0,
                "used_count": c.used_count or 0,
            }
            for c in q.all()
        ]

        excluded_q = db.query(models.Clip).filter(models.Clip.has_real_person == True)  # noqa: E712
        if game_filter:
            excluded_q = excluded_q.join(models.Source).filter(models.Source.game == game_filter)
        excluded_real_person = excluded_q.count()

    def _eff_score(c: dict) -> float:
        return (c.get("hook_score") or 0.0) - 0.15 * (c.get("used_count") or 0)

    # effective score 排序后取前 40 (用过的自动下沉), 再加权随机抽 12。
    # 以前喂 40 个候选给 DeepSeek Pro, 上下文和推理时间明显偏重；
    # 12 个足够生成 7-9 个场景, 也更容易稳定返回完整 JSON。
    pool.sort(key=_eff_score, reverse=True)
    pool = pool[:40]
    if len(pool) > 12:
        # 无放回的加权抽样: 给每个候选打 random()/weight 分, 取最小的 12 个
        scored = [(random.random() / max(0.05, _eff_score(c) + 0.3), c) for c in pool]
        scored.sort(key=lambda x: x[0])
        candidates = [c for _, c in scored[:12]]
    else:
        candidates = pool[:]
    random.shuffle(candidates)

    if not candidates:
        hint = "" if not excluded_real_person else f"（{excluded_real_person} 个候选因含真人被自动排除）"
        if game_filter:
            raise RuntimeError(f"游戏「{game_filter}」下没有可用片段{hint}")
        raise RuntimeError(f"素材库里没有任何可用片段{hint}")

    log_extra = f"，已自动排除 {excluded_real_person} 个含真人片段" if excluded_real_person else ""
    ctx.update(stage="script", progress=40,
               log_line=f"候选片段 {len(candidates)} 个{log_extra}, 开始生成脚本…")
    script_obj = script_mod.generate_script(
        product_name=product_name,
        product_info=product_info,
        target_duration=target_duration,
        style=style,
        candidates=candidates,
    )
    ctx.update(stage="script", progress=85, log_line="脚本内容已返回, 正在整理场景…")

    with db_session() as db:
        gen = db.get(models.Generation, gen_id)
        gen.script_json = script_obj
        gen.used_clip_ids = [s["clip_id"] for s in script_obj["scenes"]]
        gen.status = "awaiting_review"

    total_dur = float(script_obj.get("total_duration") or 0)
    ctx.update(stage="done", progress=100,
               log_line=f"脚本生成完成 ({len(script_obj['scenes'])} 个场景, 总长 ~{total_dur:.1f}s), 等待人工审核 ✋")
    return gen_id


@register("render_video")
def run_render_video(job_id: int, ctx: JobContext) -> None:
    """阶段 2: 把已审核好的 script_json 跑成最终 mp4."""
    gen_id = _gen_id_for_job(job_id)
    try:
        _run_render_video_inner(job_id, ctx)
    except Exception as e:
        _mark_generation_failed(gen_id, e)
        raise


def _run_render_video_inner(job_id: int, ctx: JobContext) -> int:
    with db_session() as db:
        job = db.get(models.Job, job_id)
        gen_id = job.target_id
        gen = db.get(models.Generation, gen_id)
        if not gen:
            raise RuntimeError(f"Generation {gen_id} not found")
        if job.status != "running":
            ctx.update(log_line="任务已被新的请求取代，跳过")
            return gen_id
        if gen.status != "queued":
            ctx.update(log_line=f"当前生成任务状态为 {gen.status}，跳过旧渲染任务")
            return gen_id
        script_obj = gen.script_json
        voice = gen.voice
        bgm_style = gen.bgm_style or settings.BGM_DEFAULT_STYLE
        bgm_file = gen.bgm_file
        bgm_category = gen.bgm_category
        bgm_start_mode = gen.bgm_start_mode or "smart"
        subtitle_font = gen.subtitle_font
        subtitle_highlight_font = gen.subtitle_highlight_font
        if not script_obj or not script_obj.get("scenes"):
            raise RuntimeError("脚本为空, 无法渲染")
        clip_ids = [int(s["clip_id"]) for s in script_obj["scenes"]]
        gen.status = "rendering"
        gen.error = None

    with db_session() as db:
        clips = db.query(models.Clip).filter(models.Clip.id.in_(clip_ids)).all()
        clip_lookup = {c.id: Path(c.file_path) for c in clips if c.file_path}

    missing = [cid for cid in clip_ids if cid not in clip_lookup]
    if missing:
        raise RuntimeError(f"脚本引用的片段已不存在: {missing}")

    out_dir = settings.storage_path / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"gen_{gen_id:05d}_{int(datetime.utcnow().timestamp())}.mp4"

    ctx.update(stage="render", progress=10, log_line="开始渲染配音与视频…")
    result = compose_mod.render_video(
        script=script_obj,
        clip_lookup=clip_lookup,
        voice=voice,
        output_path=out_path,
        bgm_style=bgm_style,
        bgm_file=bgm_file,
        bgm_category=bgm_category,
        bgm_start_mode=bgm_start_mode,
        subtitle_font=subtitle_font,
        subtitle_highlight_font=subtitle_highlight_font,
        progress_cb=lambda progress, message: ctx.update(
            stage="render",
            progress=max(10, min(90, int(progress))),
            log_line=message,
        ),
    )
    if result.get("bgm_used"):
        ctx.update(stage="render", progress=92,
                   log_line=f"已混入背景音乐: {Path(result['bgm_used']).name}")
    else:
        ctx.update(stage="render", progress=92,
                   log_line="未使用背景音乐, 输出纯人声 (可在生成页上传 BGM 启用)")
    ctx.update(stage="render", progress=95, log_line=f"视频渲染完成: {Path(result['output_path']).name}")

    pname = ""
    pinfo = None
    ggame = None
    gstyle = None
    sj = None
    with db_session() as db:
        gen = db.get(models.Generation, gen_id)
        gen.voiceover_path = result["voiceover_path"]
        gen.subtitle_path = result["subtitle_path"]
        gen.output_path = result["output_path"]
        gen.status = "done"
        gen.finished_at = datetime.utcnow()
        pname = gen.product_name or ""
        pinfo = gen.product_info
        ggame = gen.game
        gstyle = gen.style
        sj = gen.script_json
        for cid in clip_ids:
            clip = db.get(models.Clip, cid)
            if clip:
                clip.used_count = (clip.used_count or 0) + 1

    cover_abs = settings.storage_path / "output" / "covers" / f"gen_{gen_id:05d}_cover_{int(datetime.utcnow().timestamp())}.jpg"
    cover_abs.parent.mkdir(parents=True, exist_ok=True)
    ctx.update(stage="render", progress=96, log_line="生成封面图…")
    from app.llm import cover_image as cover_image_mod

    ok_cov, cerr, base_id = cover_image_mod.generate_cover_file(
        gen_id=gen_id,
        product_name=pname,
        product_info=pinfo,
        game=ggame,
        style=gstyle,
        script_json=sj,
        clip_ids=clip_ids,
        dst=cover_abs,
    )
    if ok_cov:
        with db_session() as db:
            gen2 = db.get(models.Generation, gen_id)
            if gen2:
                gen2.cover_path = str(cover_abs)
                gen2.script_json = cover_image_mod.remember_cover_base(gen2.script_json, base_id)
        ctx.update(stage="render", progress=98, log_line=f"封面已保存: {cover_abs.name}")
    else:
        tail = (cerr or "未知原因")[:1000]
        ctx.update(stage="render", progress=98, log_line=f"未生成封面: {tail}")

    ctx.update(stage="done", progress=100, log_line="生成完成 ✓")
    return gen_id


@register("generate_cover")
def run_generate_cover(job_id: int, ctx: JobContext) -> None:
    """仅重新生成封面 (成片已完成)."""
    from app.llm import cover_image as cover_image_mod

    with db_session() as db:
        job = db.get(models.Job, job_id)
        if not job or not job.target_id:
            return
        gen_id = int(job.target_id)
        gen = db.get(models.Generation, gen_id)
        if not gen:
            ctx.update(progress=100, log_line="找不到对应的生成任务")
            return
        if gen.status != "done":
            ctx.update(progress=100, log_line=f"当前状态 {gen.status} 不能重新生成封面")
            return
        pname = gen.product_name or ""
        pinfo = gen.product_info
        ggame = gen.game
        gstyle = gen.style
        sj = gen.script_json
        clip_ids = list(gen.used_clip_ids or [])
        if not clip_ids and isinstance(sj, dict):
            clip_ids = [int(s["clip_id"]) for s in sj.get("scenes", []) if s.get("clip_id") is not None]

    ctx.update(progress=10, stage="cover", log_line="准备封面素材…")
    cover_abs = settings.storage_path / "output" / "covers" / f"gen_{gen_id:05d}_cover_{int(datetime.utcnow().timestamp())}.jpg"
    cover_abs.parent.mkdir(parents=True, exist_ok=True)
    ctx.update(progress=35, stage="cover", log_line="正在生成封面图…")
    ok, cerr, base_id = cover_image_mod.generate_cover_file(
        gen_id=gen_id,
        product_name=pname,
        product_info=pinfo,
        game=ggame,
        style=gstyle,
        script_json=sj,
        clip_ids=clip_ids,
        dst=cover_abs,
        force_new_base=True,
    )
    if ok:
        ctx.update(progress=85, stage="cover", log_line="封面生成完成，正在保存…")
        with db_session() as db:
            g2 = db.get(models.Generation, gen_id)
            if g2:
                g2.cover_path = str(cover_abs)
                g2.script_json = cover_image_mod.remember_cover_base(g2.script_json, base_id)
        ctx.update(progress=100, stage="done", log_line=f"封面已更新: {cover_abs.name}")
    else:
        tail = (cerr or "未知原因")[:1200]
        ctx.update(progress=100, stage="failed", log_line=f"封面生成失败: {tail}")
