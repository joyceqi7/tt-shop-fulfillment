"""统一配置入口，从 .env 读。

使用 pydantic-settings：
- 自动从 .env 加载，也读环境变量
- 类型校验在启动时报错，避免 runtime 踩到空配置
- 通过 `get_settings()` lru_cache 单例，FastAPI 里可以 Depends 注入
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- 环境 ---
    app_env: str = Field(default="dev", description="dev / staging / prod")
    log_level: str = "INFO"

    # --- TT Open API 凭证 ---
    tts_app_key: str
    tts_app_secret: str
    tts_refresh_token: str
    tts_seller_id: str = ""
    tts_api_base: str = "https://open-api.tiktokglobalshop.com"
    tts_auth_base: str = "https://auth.tiktok-shops.com"
    tts_http_proxy: str | None = None
    tts_api_qps: int = 10

    # --- OAuth ---
    tts_service_id: str = ""
    """TT Partner Center 里 App 的 Service ID，= App ID，生成授权 URL 用。"""
    tts_authorize_base: str = "https://services.tiktokshop.com"
    """TT 授权入口的 base URL：
    - Cross-border / 全球卖家：services.tiktokshop.com
    - US Local sellers（仅美国本地店）：services.us.tiktokshop.com
    不确定时去 Partner Center → App 详情页看它给的 "Authorize" 链接是哪个 host。
    """
    tts_oauth_redirect_url: str = ""
    """OAuth 回调完整 URL（https://tts-api.sailson.com/oauth/tts/callback）；可空，仅用于内部日志 / 生成客户手册链接。"""
    oauth_state_ttl_seconds: int = 600
    """/oauth/tts/start 生成的 state 在 Redis 里保留多久（防 CSRF + 授权超时）。"""

    # --- DB / Redis ---
    database_url: str = "postgresql+asyncpg://tts:tts@localhost:5432/tts"
    redis_url: str = "redis://localhost:6379/0"

    # --- 加密 ---
    # Fernet 主密钥；生成方式：python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    fernet_key: str

    # --- 可观测性 ---
    sentry_dsn: str | None = None
    feishu_webhook_url: str | None = None

    # --- 业务参数 ---
    code_lock_timeout_seconds: int = 600
    reconcile_interval_seconds: int = 300
    reconcile_window_hours: int = 24
    token_refresh_buffer_seconds: int = 600
    """距离 access_token 过期还剩多少秒时就触发刷新（提前量，避免用到刚过期的 token）。"""

    stock_alert_threshold: int = 50
    """任何一条 active sku_mapping 的 count_free 低于此值 → watchdog 告警。"""
    supplier_delivery_timeout_seconds: int = Field(
        default=300,
        validation_alias=AliasChoices(
            "supplier_delivery_timeout_seconds",
            "wgcards_delivery_timeout_seconds",
        ),
    )
    """供应商 API 轮询取卡超时（秒）。"""
    supplier_poll_interval_seconds: int = Field(
        default=5,
        validation_alias=AliasChoices(
            "supplier_poll_interval_seconds",
            "wgcards_poll_interval_seconds",
        ),
    )
    """供应商 API 轮询间隔（秒）。"""
    token_expire_alert_seconds: int = 21600
    """access_token 剩余有效期低于此阈值 → watchdog 告警（refresh 失败的前哨）。"""
    reconciliation_lag_alert_seconds: int = 7200
    """某店 reconciliation:last_ts 比 now 滞后超过此值 → watchdog 告警（对账 Job 堵了）。"""
    stale_shipment_alert_seconds: int = 14400
    """订单仍有 pending 行且超过此秒数未发货 → 飞书告警（默认 4 小时）。"""

    webhook_verify_disabled: bool = False
    """联调期可设为 true 跳过 webhook 验签；上线必须设回 false。"""

    admin_token: str | None = None
    """管理 API（/admin/*）的鉴权 token。未设置时 /admin/* 整组路由 503 禁用。"""

    # --- Admin Console（v2 后台管理网站，cookie session）---
    admin_username: str | None = None
    """后台登录用户名。未配置时 /admin/auth/* 503 禁用。"""
    admin_password_hash: str | None = None
    """bcrypt 哈希后的密码。生成方式：python scripts/gen_admin_hash.py"""
    admin_session_secret: str | None = None
    """HMAC-SHA256 签发 cookie session 的主密钥（≥ 32 字节随机串）。env: ADMIN_SESSION_SECRET"""
    admin_session_cookie_domain: str | None = None
    """cookie 作用域；生产填 .sailson.com 让 admin / api 子域共享。本地 dev 留空。"""
    admin_session_ttl_seconds: int = 14 * 86400
    """cookie 有效期，默认 14 天。env: ADMIN_SESSION_TTL_SECONDS"""

    # --- 财务参数 ---
    tt_platform_commission_rate: float = 0.06
    """TT 平台抽成估算比例（默认 6%）；接 TT 结算单 API 后可改实算。"""
    price_guard_interval_minutes: int = 20
    """调价倒挂预警 cron 间隔（分钟）。"""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # pydantic-settings 把 .env 里 `KEY=` 读成 "" 而不是 None —— 对 Optional[str] 字段很坑，
    # httpx/sentry SDK 等传入空串会炸（"Unknown scheme for proxy URL URL('')" 就是这么来的）。
    # 这里把所有空串统一回落到 None。
    @field_validator(
        "tts_http_proxy",
        "sentry_dsn",
        "feishu_webhook_url",
        "admin_token",
        "admin_username",
        "admin_password_hash",
        "admin_session_secret",
        "admin_session_cookie_domain",
        mode="before",
    )
    @classmethod
    def _empty_str_to_none(cls, v: object) -> object:
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
