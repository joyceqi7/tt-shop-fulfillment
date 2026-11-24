"""把 storage/ 下的文件以 /files/<sub>/<name> 暴露给前端直接 <video> 播放。"""
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.core.config import settings

router = APIRouter(prefix="/files", tags=["files"])

ALLOWED_SUBDIRS = {"raw", "processed", "clips", "audio", "output", "thumbnails", "bgm"}


@router.get("/{subdir}/{filepath:path}")
def serve_file(
    subdir: str,
    filepath: str,
    dl: int = Query(0, description="1=以附件下载（Content-Disposition: attachment）；0=内联预览"),
):
    """filepath 可含子目录，例如 output/covers/gen_00001_cover.jpg（单层 {filename} 无法匹配）。"""
    if subdir not in ALLOWED_SUBDIRS:
        raise HTTPException(404, "not allowed")
    filepath = filepath.lstrip("/")
    if ".." in filepath or filepath.startswith(("/", "\\")):
        raise HTTPException(400, "invalid path")
    fp = (settings.storage_path / subdir / filepath).resolve()
    base = (settings.storage_path / subdir).resolve()
    try:
        fp.relative_to(base)
    except ValueError:
        raise HTTPException(400, "invalid path")
    if not fp.exists() or not fp.is_file():
        raise HTTPException(404, "file not found")
    # dl=1：浏览器「另存为」或 <a download> 时更稳定；<img>/<video> 不要带 dl
    if dl == 1:
        return FileResponse(fp, filename=fp.name)
    return FileResponse(fp)


def to_url(path: str | None) -> str | None:
    """把绝对文件路径转成 /files/<sub>/<name> 形式（前端用）。"""
    if not path:
        return None
    p = Path(path)
    try:
        rel = p.resolve().relative_to(settings.storage_path.resolve())
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) < 2:
        return None
    return f"/files/{parts[0]}/{'/'.join(parts[1:])}"
