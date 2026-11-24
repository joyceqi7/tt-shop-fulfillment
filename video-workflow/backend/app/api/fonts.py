from fastapi import APIRouter

from app.core import font_registry


router = APIRouter(prefix="/fonts", tags=["fonts"])


@router.get("")
def list_fonts() -> dict:
    return {"fonts": font_registry.list_fonts()}
