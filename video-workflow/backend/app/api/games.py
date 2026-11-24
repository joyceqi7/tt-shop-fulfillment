from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import models
from app.core.db import get_db

router = APIRouter(prefix="/games", tags=["games"])


@router.get("")
def list_games(db: Session = Depends(get_db)) -> list[dict]:
    """返回所有出现过的游戏 + 各自素材数, 给前端做下拉选择."""
    rows = (
        db.query(models.Source.game, func.count(models.Source.id))
        .filter(models.Source.game.is_not(None))
        .group_by(models.Source.game)
        .order_by(func.count(models.Source.id).desc())
        .all()
    )
    return [{"name": g, "source_count": n} for g, n in rows if g]
