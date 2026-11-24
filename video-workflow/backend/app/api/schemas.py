from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field


class IngestRequest(BaseModel):
    urls: list[str] = Field(..., min_length=1)
    game: Optional[str] = None
    crop_top: Optional[int] = None
    crop_bottom: Optional[int] = None


class SourceOut(BaseModel):
    id: int
    url: str
    platform: Optional[str] = None
    game: Optional[str] = None
    original_title: Optional[str] = None
    author: Optional[str] = None
    duration: Optional[float] = None
    thumbnail_path: Optional[str] = None
    thumbnail_url: Optional[str] = None
    processed_url: Optional[str] = None
    status: str
    error: Optional[str] = None
    job_id: Optional[int] = None
    job_status: Optional[str] = None
    job_progress: Optional[float] = None
    job_stage: Optional[str] = None
    job_error: Optional[str] = None
    job_log: Optional[str] = None
    job_started_at: Optional[datetime] = None
    job_finished_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class SourceUpdate(BaseModel):
    game: Optional[str] = None


class ClipOut(BaseModel):
    id: int
    source_id: int
    game: Optional[str] = None
    start: float
    end: float
    duration: float
    file_path: Optional[str] = None
    file_url: Optional[str] = None
    thumbnail_path: Optional[str] = None
    thumbnail_url: Optional[str] = None
    transcript: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    content_type: Optional[str] = None
    mood: Optional[str] = None
    hook_score: Optional[float] = None
    has_real_person: Optional[bool] = False
    real_person_note: Optional[str] = None
    used_count: Optional[int] = 0
    created_at: datetime


class ClipUpdate(BaseModel):
    """允许用户手动覆盖 VLM 的真人判断."""
    has_real_person: Optional[bool] = None

    class Config:
        from_attributes = True


class JobOut(BaseModel):
    id: int
    kind: str
    target_id: Optional[int] = None
    status: str
    progress: float
    stage: Optional[str] = None
    log: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class GenerateRequest(BaseModel):
    product_name: str
    product_info: str = ""
    target_duration: int = 30
    style: str = "energetic"
    voice: Optional[str] = None
    game: Optional[str] = None
    bgm_style: Optional[str] = None  # energetic / chill / cinematic / hiphop / none
    bgm_file: Optional[str] = None   # library/foo.mp3; 空 = 随机
    bgm_category: Optional[str] = None
    bgm_start_mode: Optional[str] = "smart"
    subtitle_font: Optional[str] = None
    subtitle_highlight_font: Optional[str] = None


class SceneEdit(BaseModel):
    """脚本审核阶段, 用户可编辑的字段."""
    clip_id: int
    voiceover: str


class ScriptPatch(BaseModel):
    scenes: list[SceneEdit]


class CoverTextPatch(BaseModel):
    title: Optional[str] = None
    offer: Optional[str] = None
    kicker: Optional[str] = None
    line2: Optional[str] = None
    line3: Optional[str] = None


class GenerationOut(BaseModel):
    id: int
    game: Optional[str] = None
    product_name: Optional[str]
    product_info: Optional[str]
    target_duration: Optional[int]
    style: Optional[str]
    voice: Optional[str]
    bgm_style: Optional[str] = None
    bgm_file: Optional[str] = None
    bgm_category: Optional[str] = None
    bgm_start_mode: Optional[str] = None
    subtitle_font: Optional[str] = None
    subtitle_highlight_font: Optional[str] = None
    script_json: Optional[Any] = None
    used_clip_ids: Optional[list[int]] = None
    output_path: Optional[str] = None
    output_url: Optional[str] = None
    subtitle_path: Optional[str] = None
    subtitle_url: Optional[str] = None
    voiceover_path: Optional[str] = None
    voiceover_url: Optional[str] = None
    cover_path: Optional[str] = None
    cover_url: Optional[str] = None
    status: str
    error: Optional[str] = None
    created_at: datetime
    finished_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class StatsOut(BaseModel):
    sources_total: int
    sources_done: int
    clips_total: int
    generations_total: int
    jobs_running: int
    jobs_queued: int
