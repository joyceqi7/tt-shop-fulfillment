"""SQLAlchemy ORM models.

数据模型说明:
- Source: 一次下载的原始视频
- Clip:   原始视频切出来的小片段（一个 shot）
- Job:    后台处理任务
- Generation: 一次"生成新视频"的请求 + 结果
"""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Text, ForeignKey, JSON, Boolean
)
from sqlalchemy.orm import relationship

from app.core.db import Base


class Source(Base):
    """一次下载的原始视频。"""
    __tablename__ = "sources"

    id = Column(Integer, primary_key=True)
    url = Column(String(1024), nullable=False)
    platform = Column(String(32))                # douyin / youtube / bilibili / ...
    game = Column(String(64), index=True)        # 例如 "Diablo 4" / "Call of Duty MW3"
    original_title = Column(String(512))
    author = Column(String(256))
    duration = Column(Float)                     # seconds
    width = Column(Integer)
    height = Column(Integer)

    raw_path = Column(String(512))               # storage/raw/xxx.mp4
    processed_path = Column(String(512))         # 去水印去音轨后
    thumbnail_path = Column(String(512))
    transcript = Column(Text)                    # 全片 ASR 文本
    transcript_segments = Column(JSON)           # [{start, end, text}]
    language = Column(String(16))
    crop_top = Column(Integer, default=0)
    crop_bottom = Column(Integer, default=0)

    status = Column(String(32), default="pending")  # pending/processing/done/failed
    error = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    clips = relationship("Clip", back_populates="source", cascade="all, delete-orphan")


class Clip(Base):
    """从 Source 切出来的一个镜头片段，是后续生成视频的最小单位。"""
    __tablename__ = "clips"

    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=False)

    start = Column(Float, nullable=False)
    end = Column(Float, nullable=False)
    duration = Column(Float, nullable=False)

    file_path = Column(String(512))              # storage/clips/xxx.mp4
    thumbnail_path = Column(String(512))

    transcript = Column(Text)                    # 此片段对应的 ASR 文字
    description = Column(Text)                   # LLM 生成的视觉描述
    tags = Column(JSON)                          # ["开箱", "惊讶", "游戏画面"]
    content_type = Column(String(64))            # hook / problem / product / proof / cta / broll
    mood = Column(String(64))                    # exciting / calm / funny / ...
    hook_score = Column(Float, default=0.0)      # 0~1, 适合做开头的程度

    # 是否含真人 (主播脸 / 真人讲解 / 直播探头), 用于避免肖像权风险
    # 注意: 游戏内 3D 角色 / NPC / CG 过场不算真人, 不会被标记
    has_real_person = Column(Boolean, default=False, index=True)
    real_person_note = Column(String(256))       # VLM 给的判断说明 (调试用)

    used_count = Column(Integer, default=0)      # 被引用了几次

    created_at = Column(DateTime, default=datetime.utcnow)

    source = relationship("Source", back_populates="clips")


class Job(Base):
    """后台任务（每个 Source 处理过程会产生一个 ingest 任务；每次生成视频产生一个 generate 任务）。"""
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True)
    kind = Column(String(32), nullable=False)    # ingest / generate
    target_id = Column(Integer)                  # source_id 或 generation_id
    status = Column(String(32), default="queued")  # queued/running/done/failed
    progress = Column(Float, default=0.0)        # 0~100
    stage = Column(String(64))                   # download/watermark/asr/scene/tag/...
    log = Column(Text, default="")
    error = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)


class Generation(Base):
    """一次"生成新视频"的请求和产物。"""
    __tablename__ = "generations"

    id = Column(Integer, primary_key=True)

    game = Column(String(64), index=True)            # 限定从哪个游戏的素材里选
    product_name = Column(String(256))
    product_info = Column(Text)
    target_duration = Column(Integer, default=30)    # 目标时长（秒）
    style = Column(String(64), default="energetic")  # energetic / chill / urgent
    voice = Column(String(64))                       # TTS voice id
    bgm_style = Column(String(32))                   # energetic / chill / cinematic / hiphop / none
    bgm_file = Column(String(512))                   # 指定 BGM: "library/foo.mp3"; 空则随机
    bgm_category = Column(String(64))                # 指定 BGM 分类；空则全库随机
    bgm_start_mode = Column(String(32), default="smart")  # smart / start
    subtitle_font = Column(String(64))               # 普通字幕字体
    subtitle_highlight_font = Column(String(64))     # 高亮字幕字体

    script_json = Column(JSON)                       # LLM 生成的英文分镜脚本
    used_clip_ids = Column(JSON)                     # [clip_id, ...]
    voiceover_path = Column(String(512))
    subtitle_path = Column(String(512))
    output_path = Column(String(512))
    cover_path = Column(String(512))                 # AI 竖屏封面 JPEG (TikTok 上传用)

    status = Column(String(32), default="pending")
    error = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime)
