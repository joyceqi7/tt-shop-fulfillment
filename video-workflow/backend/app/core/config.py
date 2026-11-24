from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=[".env", "../.env"],
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 文案脚本模型。生产环境必须显式配置, 配置缺失时直接失败。
    LLM_BASE_URL: str = ""
    LLM_API_KEY: str = ""
    LLM_TEXT_MODEL: str = "deepseek-v4-pro"
    LLM_VISION_MODEL: str = "qwen-vl-max"

    # AI 封面：默认 DashScope 通义万相（与千问同生态，无需 OpenAI）
    # IMAGE_PROVIDER: dashscope（万相 wan2.6 同步 HTTP）| openai（兼容 images.generate）
    IMAGE_PROVIDER: str = "dashscope"
    DASHSCOPE_API_KEY: str = ""       # 空则依次用 VISION_API_KEY、LLM_API_KEY（须为阿里云 DashScope Key）
    DASHSCOPE_REGION: str = "cn"      # cn=北京 | intl=新加坡 | us=弗吉尼亚（与控制台地域一致）

    COVER_IMAGE_ENABLED: bool = True
    IMAGE_API_KEY: str = ""           # 仅 IMAGE_PROVIDER=openai 时优先；dashscope 模式可空
    IMAGE_BASE_URL: str = ""          # 仅 openai 模式
    IMAGE_MODEL: str = "wan2.6-t2i"   # dashscope 下为万相模型；openai 下如 dall-e-3
    IMAGE_SIZE: str = "1080*1920"     # dashscope: 宽*高（万相约束）；openai: 1024x1792 等
    COVER_JPEG_QUALITY: int = 92

    VISION_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    VISION_API_KEY: str = ""

    TTS_PROVIDER: str = "elevenlabs"
    OPENAI_TTS_MODEL: str = "tts-1-hd"
    OPENAI_TTS_VOICE: str = "alloy"
    ELEVENLABS_API_KEY: str = ""
    ELEVENLABS_VOICE_ID: str = "21m00Tcm4TlvDq8ikWAM"
    ELEVENLABS_MODEL: str = "eleven_multilingual_v2"
    # TTS 后处理倍速 (1.0 = 原速, 1.12 = 快 12%, 接近 180 WPM)
    TTS_SPEED: float = 1.12

    @property
    def vision_base_url(self) -> str:
        return self.VISION_BASE_URL or self.LLM_BASE_URL

    @property
    def vision_api_key(self) -> str:
        return self.VISION_API_KEY or self.LLM_API_KEY

    @property
    def image_api_key(self) -> str:
        return (self.IMAGE_API_KEY or self.LLM_API_KEY or "").strip()

    @property
    def image_base_url(self) -> str:
        return (self.IMAGE_BASE_URL or self.LLM_BASE_URL or "").strip().rstrip("/")

    @property
    def dashscope_api_key(self) -> str:
        """通义万相 / DashScope HTTP 用 Key（与千问聊天 Key 一般为同一把，在阿里云控制台创建）。"""
        return (self.DASHSCOPE_API_KEY or self.VISION_API_KEY or self.LLM_API_KEY or "").strip()

    DOUYIN_COOKIES_FILE: str = ""
    YOUTUBE_COOKIES_FILE: str = ""

    DATABASE_URL: str = "sqlite:///./storage/workflow.db"
    STORAGE_ROOT: str = "./storage"
    MAX_CONCURRENT_JOBS: int = 2

    # BGM 系统
    BGM_DIR: str = "./storage/bgm"
    BGM_DEFAULT_STYLE: str = "energetic"          # 用户没指定时的默认风格
    BGM_VOLUME_DB: float = -14.0                  # BGM 主音量 (相对配音)
    BGM_DUCK_THRESHOLD: float = 0.05              # sidechain 阈值, 配音比这响时压制 BGM
    BGM_DUCK_RATIO: float = 8.0                   # 压制比例

    WATERMARK_CROP_BOTTOM: int = 0
    WATERMARK_CROP_TOP: int = 0
    ASR_MODEL: str = "base"
    ASR_DEVICE: str = "cpu"
    ASR_LANGUAGE: str = "zh"
    FRAME_SAMPLE_INTERVAL: float = 2.0

    @property
    def storage_path(self) -> Path:
        p = Path(self.STORAGE_ROOT).resolve()
        for sub in ("raw", "processed", "clips", "audio", "output", "thumbnails"):
            (p / sub).mkdir(parents=True, exist_ok=True)
        (p / "output" / "covers").mkdir(parents=True, exist_ok=True)
        # BGM 目录结构
        bgm_root = Path(self.BGM_DIR).resolve()
        for style in ("energetic", "chill", "cinematic", "hiphop"):
            (bgm_root / style).mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()
