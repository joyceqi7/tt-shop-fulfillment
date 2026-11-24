from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core import models
from app.core.db import get_db
from app.api.schemas import JobOut

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=list[JobOut])
def list_jobs(
    db: Session = Depends(get_db),
    status: str | None = None,
    kind: str | None = None,
    target_id: int | None = None,
    limit: int = Query(40, le=200),
):
    q = db.query(models.Job)
    if status:
        q = q.filter(models.Job.status == status)
    if kind:
        q = q.filter(models.Job.kind == kind)
    if target_id is not None:
        q = q.filter(models.Job.target_id == target_id)
    return q.order_by(models.Job.id.desc()).limit(limit).all()


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: int, db: Session = Depends(get_db)):
    j = db.get(models.Job, job_id)
    if not j:
        raise HTTPException(404, "job not found")
    return j
