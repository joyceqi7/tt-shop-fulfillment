from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core import models
from app.core.db import get_db
from app.api.schemas import ClipOut, ClipUpdate
from app.api.serialize import clip_dict

router = APIRouter(prefix="/clips", tags=["clips"])


@router.get("", response_model=list[ClipOut])
def list_clips(
    db: Session = Depends(get_db),
    q: str | None = None,
    content_type: str | None = None,
    mood: str | None = None,
    game: str | None = None,
    min_hook: float | None = None,
    # 真人筛选: "exclude" 排除真人(默认) / "only" 只看真人 / "all" 全显示
    real_person: str = "exclude",
    limit: int = Query(60, le=200),
    offset: int = 0,
):
    query = db.query(models.Clip)
    if game:
        query = query.join(models.Source).filter(models.Source.game == game)
    if real_person == "exclude":
        query = query.filter(
            (models.Clip.has_real_person == False)  # noqa: E712
            | (models.Clip.has_real_person.is_(None))
        )
    elif real_person == "only":
        query = query.filter(models.Clip.has_real_person == True)  # noqa: E712
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            models.Clip.description.ilike(like),
            models.Clip.transcript.ilike(like),
        ))
    if content_type:
        query = query.filter(models.Clip.content_type == content_type)
    if mood:
        query = query.filter(models.Clip.mood == mood)
    if min_hook is not None:
        query = query.filter(models.Clip.hook_score >= min_hook)
    rows = query.order_by(models.Clip.id.desc()).offset(offset).limit(limit).all()
    return [clip_dict(c) for c in rows]


@router.get("/{clip_id}", response_model=ClipOut)
def get_clip(clip_id: int, db: Session = Depends(get_db)):
    c = db.get(models.Clip, clip_id)
    if not c:
        raise HTTPException(404, "clip not found")
    return clip_dict(c)


@router.patch("/{clip_id}", response_model=ClipOut)
def update_clip(clip_id: int, patch: ClipUpdate, db: Session = Depends(get_db)):
    c = db.get(models.Clip, clip_id)
    if not c:
        raise HTTPException(404, "clip not found")
    if patch.has_real_person is not None:
        c.has_real_person = bool(patch.has_real_person)
        # 用户手动覆盖, 把 note 标清楚
        c.real_person_note = "manually overridden by user"
    db.commit()
    db.refresh(c)
    return clip_dict(c)


@router.post("/reset-vlm-failed-real-person")
def reset_vlm_failed_real_person(
    db: Session = Depends(get_db),
    dry_run: bool = Query(False, description="True 时只统计不修改"),
):
    """一键修复: VLM 调用失败时旧版本会把整库错标真人, 这里把这一类一次性清掉。

    判定条件: real_person_note 里带 "VLM call failed" 或 "flagged as possibly real person"。
    描述/标签都没动 (依旧是 "untitled shot" / "unclassified") —— 用户重新跑 ingest 才能拿到真正的标签。
    """
    q = db.query(models.Clip).filter(
        models.Clip.has_real_person == True,  # noqa: E712
        or_(
            models.Clip.real_person_note.like("%VLM call failed%"),
            models.Clip.real_person_note.like("%flagged as possibly real person%"),
            models.Clip.real_person_note.like("%VLM returned empty%"),
        ),
    )
    affected = q.count()
    if dry_run:
        return {"affected": affected, "dry_run": True}
    q.update(
        {
            models.Clip.has_real_person: False,
            models.Clip.real_person_note: "VLM-failed flag reset; please re-ingest for real tags",
        },
        synchronize_session=False,
    )
    db.commit()
    return {"affected": affected, "dry_run": False}
