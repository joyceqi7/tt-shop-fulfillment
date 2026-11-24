"""用 yt-dlp 下载抖音 / YouTube / B 站等平台的视频。"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yt_dlp
from yt_dlp.utils import DownloadError

from app.core.config import settings
from app.core.logger import get_logger

log = get_logger(__name__)

DEFAULT_FORMAT = "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/best"
YOUTUBE_FORMAT = "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720][ext=mp4]/bv*[height<=720]+ba/b[height<=720]/bv*+ba/b"
ProgressCallback = Callable[[float, str], None]


@dataclass
class DownloadResult:
    file_path: Path
    title: str
    author: str
    duration: float
    width: int
    height: int
    platform: str


def detect_platform(url: str) -> str:
    u = url.lower()
    if "store.steampowered.com/app/" in u:
        return "steam"
    if "douyin.com" in u or "iesdouyin.com" in u:
        return "douyin"
    if "tiktok.com" in u:
        return "tiktok"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if "bilibili.com" in u or "b23.tv" in u:
        return "bilibili"
    return "unknown"


def download(
    url: str,
    target_dir: Path | None = None,
    *,
    progress_cb: ProgressCallback | None = None,
) -> DownloadResult:
    target_dir = target_dir or (settings.storage_path / "raw")
    target_dir.mkdir(parents=True, exist_ok=True)

    platform = detect_platform(url)
    try:
        return _download_with_ytdlp(url, target_dir=target_dir, platform=platform, progress_cb=progress_cb)
    except DownloadError as e:
        if platform == "steam":
            log.info("[download] yt-dlp 未抓到 Steam 页面视频，尝试 Steam appdetails fallback: %s", url)
            return _download_steam_trailer(
                url,
                target_dir=target_dir,
                original_error=str(e),
                progress_cb=progress_cb,
            )
        raise RuntimeError(_friendly_download_error(platform, str(e))) from e
    except Exception as e:
        if platform == "steam":
            log.info("[download] Steam 页面常规下载失败，尝试 appdetails fallback: %s", url)
            return _download_steam_trailer(
                url,
                target_dir=target_dir,
                original_error=str(e),
                progress_cb=progress_cb,
            )
        raise


def _download_with_ytdlp(
    url: str,
    *,
    target_dir: Path,
    platform: str,
    title_override: str | None = None,
    author_override: str | None = None,
    progress_cb: ProgressCallback | None = None,
) -> DownloadResult:
    ydl_opts = _ytdlp_opts(target_dir=target_dir, platform=platform, progress_cb=progress_cb)

    log.info("[download] start: %s (%s)", url, platform)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)

    fp = _resolve_downloaded_file(filename)

    return DownloadResult(
        file_path=fp,
        title=_clean(title_override or info.get("title") or ""),
        author=_clean(author_override or info.get("uploader") or info.get("channel") or ""),
        duration=float(info.get("duration") or 0.0),
        width=int(info.get("width") or 0),
        height=int(info.get("height") or 0),
        platform=platform,
    )


def _ytdlp_opts(
    *,
    target_dir: Path,
    platform: str,
    progress_cb: ProgressCallback | None = None,
) -> dict:
    ydl_opts: dict = {
        "outtmpl": str(target_dir / "%(id)s_%(epoch)s.%(ext)s"),
        "format": YOUTUBE_FORMAT if platform == "youtube" else DEFAULT_FORMAT,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "concurrent_fragment_downloads": 4,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
        },
    }
    if platform == "youtube":
        ydl_opts["js_runtimes"] = {"deno": {}}
        ydl_opts["remote_components"] = ["ejs:github"]
    if progress_cb:
        ydl_opts["progress_hooks"] = [_make_progress_hook(progress_cb)]

    cookiefile = _cookiefile_for_platform(platform)
    if cookiefile:
        ydl_opts["cookiefile"] = cookiefile
    return ydl_opts


def _make_progress_hook(progress_cb: ProgressCallback):
    state = {"last_emit": 0.0, "last_percent": -1.0}

    def hook(d: dict) -> None:
        status = d.get("status")
        if status == "downloading":
            total = float(d.get("total_bytes") or d.get("total_bytes_estimate") or 0)
            downloaded = float(d.get("downloaded_bytes") or 0)
            percent = max(0.0, min(100.0, downloaded * 100 / total)) if total else 0.0
            now = time.monotonic()
            if percent < state["last_percent"] + 5 and now - state["last_emit"] < 5:
                return
            state["last_emit"] = now
            state["last_percent"] = percent
            parts = [f"下载中 {percent:.0f}%"]
            speed = d.get("speed")
            eta = d.get("eta")
            if speed:
                parts.append(f"{_format_bytes(float(speed))}/s")
            if eta is not None:
                parts.append(f"剩余 {_format_duration(float(eta))}")
            progress_cb(percent, " / ".join(parts))
        elif status == "finished":
            filename = Path(str(d.get("filename") or "")).name
            message = f"下载分片完成: {filename}" if filename else "下载分片完成"
            progress_cb(100.0, message)

    return hook


def _format_bytes(value: float) -> str:
    unit = "B"
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            break
        value /= 1024
    return f"{value:.1f}{unit}"


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{sec:02d}s"
    return f"{sec}s"


def _cookiefile_for_platform(platform: str) -> str | None:
    cookiefile = ""
    if platform == "douyin":
        cookiefile = settings.DOUYIN_COOKIES_FILE
    elif platform == "youtube":
        cookiefile = settings.YOUTUBE_COOKIES_FILE
    cookiefile = (cookiefile or "").strip()
    if not cookiefile:
        return None
    path = Path(cookiefile)
    if not path.exists():
        raise RuntimeError(
            f"{platform} cookies 文件不存在: {cookiefile}. "
            "请把 cookies.txt 放到 /data/workflow/cookies/ 并在 .env.production 配置绝对路径。"
        )
    if not path.is_file():
        raise RuntimeError(f"{platform} cookies 路径不是文件: {cookiefile}")
    return cookiefile


def _resolve_downloaded_file(filename: str) -> Path:
    fp = Path(filename)
    if not fp.exists():
        alt = fp.with_suffix(".mp4")
        if alt.exists():
            fp = alt
        else:
            raise FileNotFoundError(f"yt-dlp 报告下载完成但找不到文件: {filename}")
    return fp


def _download_steam_trailer(
    url: str,
    *,
    target_dir: Path,
    original_error: str,
    progress_cb: ProgressCallback | None = None,
) -> DownloadResult:
    app_id = _steam_app_id(url)
    if not app_id:
        raise RuntimeError(
            "Steam 链接不是标准商品页，无法识别 app_id。请使用形如 "
            "https://store.steampowered.com/app/123456/name/ 的链接。"
        )

    data = _fetch_steam_appdetails(app_id)
    app = data.get(str(app_id), {})
    if not app.get("success"):
        raise RuntimeError(f"Steam appdetails 没有返回商品数据 app_id={app_id}: {original_error}")

    detail = app.get("data") or {}
    movies = detail.get("movies") or []
    if not movies:
        raise RuntimeError(
            f"Steam 商品页没有可抓取的 trailer 视频 app_id={app_id}。"
            "请下载视频文件后走本地上传，或粘贴具体视频链接。"
        )

    movie = next((m for m in movies if m.get("highlight")), movies[0])
    media_url = movie.get("hls_h264") or movie.get("dash_h264") or movie.get("dash_av1")
    if not media_url:
        raise RuntimeError(
            f"Steam trailer 没有 hls/dash 视频源 app_id={app_id}。"
            "请下载视频文件后走本地上传。"
        )

    title = " - ".join(
        part for part in [str(detail.get("name") or "").strip(), str(movie.get("name") or "").strip()] if part
    )
    return _download_with_ytdlp(
        media_url,
        target_dir=target_dir,
        platform="steam",
        title_override=title or f"Steam app {app_id} trailer",
        author_override="Steam",
        progress_cb=progress_cb,
    )


def _steam_app_id(url: str) -> int | None:
    m = re.search(r"store\.steampowered\.com/app/(\d+)", url, flags=re.I)
    if not m:
        return None
    return int(m.group(1))


def _fetch_steam_appdetails(app_id: int) -> dict:
    query = urllib.parse.urlencode(
        {
            "appids": str(app_id),
            "filters": "basic,movies",
            "cc": "us",
            "l": "english",
        }
    )
    req = urllib.request.Request(
        f"https://store.steampowered.com/api/appdetails?{query}",
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - fixed Steam API endpoint
        return json.loads(resp.read().decode("utf-8"))


def _friendly_download_error(platform: str, message: str) -> str:
    if platform == "youtube" and re.search(r"sign in|not a bot|confirm you", message, flags=re.I):
        return (
            f"{message}\n"
            "YouTube 需要登录态 cookies。请导出 Netscape cookies.txt，放到 "
            "/data/workflow/cookies/youtube.txt，并配置 "
            "YOUTUBE_COOKIES_FILE=/data/workflow/cookies/youtube.txt 后重启 workflow-api。"
        )
    if platform == "youtube" and re.search(r"requested format is not available", message, flags=re.I):
        return (
            f"{message}\n"
            "YouTube 没有返回当前格式可下载的视频流。通常是 yt-dlp 或 JS 解析环境过旧，"
            "请更新 workflow 镜像后重试；急用时先下载视频文件走本地上传。"
        )
    if platform == "youtube" and re.search(r"remote components|challenge solving failed|Only images", message, flags=re.I):
        return (
            f"{message}\n"
            "YouTube JS challenge 解析组件不可用。请确认 workflow 服务可访问 GitHub，"
            "或先在本地下载视频后上传。"
        )
    return message


def _clean(s: str) -> str:
    s = re.sub(r"[\r\n\t]+", " ", s).strip()
    return s[:500]
