from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core import models
from app.core.db import get_db
from app.api.schemas import StatsOut

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("", response_model=StatsOut)
def stats(db: Session = Depends(get_db)):
    return StatsOut(
        sources_total=db.query(models.Source).count(),
        sources_done=db.query(models.Source).filter(models.Source.status == "done").count(),
        clips_total=db.query(models.Clip).count(),
        generations_total=db.query(models.Generation).count(),
        jobs_running=db.query(models.Job).filter(models.Job.status == "running").count(),
        jobs_queued=db.query(models.Job).filter(models.Job.status == "queued").count(),
    )
