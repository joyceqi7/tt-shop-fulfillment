"""BGM 池接口: 查询 / 上传 / 删除 / 试听。

设计取舍:
- 服务器部署后, 用户没法 ssh 拖文件进 storage/bgm/, 必须给上传接口。
- 单文件上限 50MB (1 首 5 分钟 mp3 约 5MB, 50MB 足够任何用法)。
- 文件名做 _safe_filename 清洗, 防路径穿越和奇怪字符。
- 新版统一放在 storage/bgm/library；旧的 style 目录仍会兼容读取，避免历史文件消失。
"""
import time
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.core import bgm_library
from app.core.logger import get_logger


log = get_logger(__name__)
router = APIRouter(prefix="/bgm", tags=["bgm"])

_MAX_BGM_BYTES = 50 * 1024 * 1024  # 50 MB


def _safe_filename(name: str) -> str:
    return bgm_library.safe_filename(name)


def _style_dir(style: str) -> Path:
    try:
        return bgm_library.style_dir(style)
    except ValueError as e:
        raise HTTPException(400, "unknown style") from e


def _resolve_safely(style: str, filename: str) -> Path:
    """防穿越: 确保最终路径在 style 目录里, 防止 ../../etc/passwd 之类。"""
    fp = bgm_library.resolve_track(style, filename)
    if not fp:
        raise HTTPException(404, "file not found")
    return fp


@router.get("/styles")
def list_styles() -> dict:
    """返回统一 BGM 库；兼容老 style 目录，前端不再暴露风格选择。"""
    bgm_root = bgm_library.bgm_root()
    meta = bgm_library.ensure_metadata_for_files()
    out = []
    for style in (bgm_library.LIBRARY_STYLE, *bgm_library.LEGACY_STYLES):
        d = bgm_root / style
        files = []
        if d.exists():
            files = [
                p.name for p in d.iterdir()
                if p.is_file() and p.suffix.lower() in bgm_library.AUDIO_EXT
            ]
        out.append({
            "style": style,
            "count": len(files),
            "files": sorted(files),
        })
    all_files = []
    for item in out:
        for filename in item["files"]:
            key = bgm_library.track_key(item["style"], filename)
            info = meta.get(key, {})
            all_files.append({
                "style": item["style"],
                "filename": filename,
                "url": f"/workflow-files/bgm/{item['style']}/{filename}",
                "category": info.get("category") or "未分类",
                "bpm": info.get("bpm"),
                "duration": info.get("duration"),
                "beat_count": len(info.get("beat_times") or []),
                "analysis_status": info.get("analysis_status") or "pending",
                "mood_tags": info.get("mood_tags") or [],
            })
    return {
        "default_style": bgm_library.LIBRARY_STYLE,
        "bgm_dir": str(bgm_root),
        "max_upload_mb": _MAX_BGM_BYTES // (1024 * 1024),
        "categories": list(bgm_library.BGM_CATEGORIES),
        "styles": out,
        "files": all_files,
    }


@router.post("/upload")
async def upload_bgm(
    style: str = Form(bgm_library.LIBRARY_STYLE, description="兼容字段；新版统一上传到 library"),
    category: str = Form("未分类"),
    files: list[UploadFile] = File(..., description="一次可上传多个 mp3/m4a/wav/ogg/flac"),
) -> dict:
    """上传 BGM 文件到统一 library 目录。"""
    if not files:
        raise HTTPException(400, "no files")
    style = bgm_library.LIBRARY_STYLE
    category = bgm_library.normalize_category(category)
    target_dir = _style_dir(style)

    saved: list[str] = []
    errors: list[str] = []
    analyzed: list[dict] = []
    for f in files:
        orig = f.filename or "bgm"
        ext = Path(orig).suffix.lower()
        if ext not in bgm_library.AUDIO_EXT:
            errors.append(f"{orig}: 不支持的音频格式 {ext or '(no ext)'}")
            continue

        stem = _safe_filename(Path(orig).stem)
        # 用时间戳兜底防重名, 重名也不会覆盖
        dest = target_dir / f"{stem}_{int(time.time()*1000)}{ext}"

        size = 0
        too_large = False
        try:
            with dest.open("wb") as out:
                while True:
                    chunk = await f.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > _MAX_BGM_BYTES:
                        too_large = True
                        break
                    out.write(chunk)
        except Exception as e:
            log.exception("[bgm] 写入失败 %s", dest)
            dest.unlink(missing_ok=True)
            errors.append(f"{orig}: 写入失败 {e}")
            continue
        finally:
            try:
                await f.close()
            except Exception:
                pass

        if too_large:
            dest.unlink(missing_ok=True)
            errors.append(f"{orig}: 超过 {_MAX_BGM_BYTES // (1024*1024)}MB 上限")
            continue
        if size == 0:
            dest.unlink(missing_ok=True)
            errors.append(f"{orig}: 空文件")
            continue
        saved.append(dest.name)
        analyzed.append(bgm_library.upsert_track_metadata(style, dest, category=category, analyze=True))

    if errors and not saved:
        raise HTTPException(400, "; ".join(errors))

    return {"saved": saved, "errors": errors, "style": style, "category": category, "analyzed": analyzed}


@router.patch("/{style}/{filename}")
def update_bgm(style: str, filename: str, payload: dict) -> dict:
    """更新 BGM 分类。"""
    category = bgm_library.normalize_category(str(payload.get("category") or "未分类"))
    meta = bgm_library.update_track_category(style, filename, category)
    if meta is None:
        raise HTTPException(404, "file not found")
    return {"ok": True, "style": style, "filename": filename, "category": category, "meta": meta}


@router.post("/{style}/{filename}/analyze")
def analyze_bgm(style: str, filename: str) -> dict:
    """为库内已有 BGM 重新做卡点分析。"""
    fp = _resolve_safely(style, filename)
    meta_map = bgm_library.ensure_metadata_for_files()
    key = bgm_library.track_key(style, filename)
    current = meta_map.get(key, {})
    category = current.get("category") or "未分类"
    meta = bgm_library.upsert_track_metadata(style, fp, category=category, analyze=True)
    return {"ok": True, "style": style, "filename": filename, "meta": meta}


@router.delete("/{style}/{filename}")
def delete_bgm(style: str, filename: str) -> dict:
    """删除指定 style 目录下的 BGM 文件。"""
    fp = _resolve_safely(style, filename)
    if not fp.exists() or not fp.is_file():
        raise HTTPException(404, "file not found")
    fp.unlink()
    bgm_library.delete_track_metadata(style, filename)
    return {"ok": True, "deleted": filename, "style": style}


@router.get("/{style}/{filename}")
def preview_bgm(style: str, filename: str):
    """在线试听 BGM。"""
    fp = _resolve_safely(style, filename)
    if not fp.exists() or not fp.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(fp)
