from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core import models
from app.core import bgm_library
from app.core.db import get_db
from app.api.schemas import CoverTextPatch, GenerateRequest, GenerationOut, ScriptPatch
from app.api.serialize import generation_dict, clip_dict
from app.workers import queue

router = APIRouter(prefix="/generations", tags=["generations"])


def _cancel_open_jobs(db: Session, *, gen_id: int, kinds: set[str]) -> None:
    """同一个 Generation 重新排任务时，取消之前还没结束的同类任务。

    否则用户连续点“重新生成”或容器重启后，旧 queued/running job 可能继续跑，
    再把旧错误写回同一个 Generation，看起来像“新配置还在报旧错误”。
    """
    rows = (
        db.query(models.Job)
        .filter(models.Job.target_id == gen_id)
        .filter(models.Job.kind.in_(kinds))
        .filter(models.Job.status.in_(("queued", "running")))
        .all()
    )
    for j in rows:
        j.status = "failed"
        j.error = "已被新的任务取代"


@router.post("", response_model=GenerationOut)
def create_generation(req: GenerateRequest, db: Session = Depends(get_db)):
    gen = models.Generation(
        game=(req.game or "").strip() or None,
        product_name=req.product_name,
        product_info=req.product_info,
        target_duration=req.target_duration,
        style=req.style,
        voice=req.voice,
        bgm_style=(req.bgm_style or "").strip().lower() or None,
        bgm_file=(req.bgm_file or "").strip() or None,
        bgm_category=bgm_library.normalize_category(req.bgm_category) if req.bgm_category else None,
        bgm_start_mode=(req.bgm_start_mode or "smart").strip().lower() or "smart",
        subtitle_font=(req.subtitle_font or "").strip() or None,
        subtitle_highlight_font=(req.subtitle_highlight_font or "").strip() or None,
        status="queued",
    )
    db.add(gen)
    db.commit()
    db.refresh(gen)
    queue.enqueue("generate_script", gen.id)
    return generation_dict(gen)


@router.get("", response_model=list[GenerationOut])
def list_generations(db: Session = Depends(get_db), limit: int = 30):
    rows = db.query(models.Generation).order_by(models.Generation.id.desc()).limit(limit).all()
    return [generation_dict(g) for g in rows]


@router.get("/{gen_id}", response_model=GenerationOut)
def get_generation(gen_id: int, db: Session = Depends(get_db)):
    g = db.get(models.Generation, gen_id)
    if not g:
        raise HTTPException(404, "generation not found")
    return generation_dict(g)


@router.get("/{gen_id}/script-clips")
def get_script_clips(gen_id: int, db: Session = Depends(get_db)):
    """审核界面需要每个场景对应 clip 的缩略图等信息."""
    g = db.get(models.Generation, gen_id)
    if not g or not g.script_json:
        raise HTTPException(404, "script not ready")
    clip_ids = [int(s["clip_id"]) for s in g.script_json.get("scenes", [])]
    clips = db.query(models.Clip).filter(models.Clip.id.in_(clip_ids)).all()
    return {c.id: clip_dict(c) for c in clips}


@router.patch("/{gen_id}/script", response_model=GenerationOut)
def update_script(gen_id: int, patch: ScriptPatch, db: Session = Depends(get_db)):
    """人工编辑每个场景的 voiceover 文案 (审核环节核心)."""
    g = db.get(models.Generation, gen_id)
    if not g:
        raise HTTPException(404, "generation not found")
    if g.status not in {"awaiting_review", "failed"}:
        raise HTTPException(409, f"当前状态 {g.status} 不能编辑脚本")
    if not g.script_json or not g.script_json.get("scenes"):
        raise HTTPException(400, "脚本还没生成完成")

    # 用 clip_id 匹配编辑项, 只覆盖 voiceover 字段, 顺序保持脚本原顺序
    edits_by_id = {}
    for e in patch.scenes:
        edits_by_id.setdefault(int(e.clip_id), []).append(e.voiceover.strip())

    script = dict(g.script_json)
    new_scenes = []
    seen_counter: dict[int, int] = {}
    for scene in script.get("scenes", []):
        cid = int(scene["clip_id"])
        idx = seen_counter.get(cid, 0)
        seen_counter[cid] = idx + 1
        new_scene = dict(scene)
        if cid in edits_by_id and idx < len(edits_by_id[cid]):
            new_scene["voiceover"] = edits_by_id[cid][idx][:400]
        new_scenes.append(new_scene)
    script["scenes"] = new_scenes
    g.script_json = script
    db.commit()
    db.refresh(g)
    return generation_dict(g)


@router.post("/{gen_id}/regenerate-script", response_model=GenerationOut)
def regenerate_script(gen_id: int, db: Session = Depends(get_db)):
    """对整体脚本不满意时, 让 LLM 重新生成一版 (走相同的 generate_script 任务)."""
    g = db.get(models.Generation, gen_id)
    if not g:
        raise HTTPException(404, "generation not found")
    if g.status in {"scripting", "rendering"}:
        raise HTTPException(409, f"当前状态 {g.status} 不能重新生成脚本")
    g.status = "queued"
    g.error = None
    _cancel_open_jobs(db, gen_id=g.id, kinds={"generate_script"})
    db.commit()
    db.refresh(g)
    queue.enqueue("generate_script", g.id)
    return generation_dict(g)


@router.post("/{gen_id}/render", response_model=GenerationOut)
def render(gen_id: int, db: Session = Depends(get_db)):
    """用户确认脚本后, 触发渲染."""
    g = db.get(models.Generation, gen_id)
    if not g:
        raise HTTPException(404, "generation not found")
    if g.status not in {"awaiting_review", "failed"}:
        raise HTTPException(409, f"当前状态 {g.status} 不能渲染")
    if not g.script_json or not g.script_json.get("scenes"):
        raise HTTPException(400, "脚本尚未生成, 无法渲染")
    g.status = "queued"
    g.error = None
    _cancel_open_jobs(db, gen_id=g.id, kinds={"render_video"})
    db.commit()
    db.refresh(g)
    queue.enqueue("render_video", g.id)
    return generation_dict(g)


@router.post("/{gen_id}/regenerate-cover", response_model=GenerationOut)
def regenerate_cover(gen_id: int, patch: CoverTextPatch | None = None, db: Session = Depends(get_db)):
    """成片完成后可单独排队重跑 AI 封面 (不重新渲染视频)."""
    g = db.get(models.Generation, gen_id)
    if not g:
        raise HTTPException(404, "generation not found")
    if g.status != "done":
        raise HTTPException(409, "请先完成视频渲染后再生成封面")
    if patch is not None:
        script = dict(g.script_json or {})
        # None 表示保持默认推导；空字符串表示这一行不画。
        script["cover_text"] = {
            "title": patch.title,
            "offer": patch.offer,
            "kicker": patch.kicker,
            "line2": patch.line2,
            "line3": patch.line3,
        }
        g.script_json = script
        db.commit()
        db.refresh(g)
    queue.enqueue("generate_cover", g.id)
    return generation_dict(g)
