"""应用配置：通过环境变量注入，禁止硬编码任何密钥。"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM (OpenAI-compatible)
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_base_url: str = Field(default="https://api.openai.com/v1", alias="LLM_BASE_URL")
    llm_model: str = Field(default="gpt-4o-mini", alias="LLM_MODEL")

    # Embedding (OpenAI-compatible) —— openai：远程 API；local：本地 fastembed
    embedding_backend: str = Field(default="openai", alias="EMBEDDING_BACKEND")
    embedding_model: str = Field(default="text-embedding-3-small", alias="EMBEDDING_MODEL")
    local_embedding_model: str = Field(
        default="BAAI/bge-small-zh-v1.5", alias="LOCAL_EMBEDDING_MODEL"
    )

    # 高德地图
    amap_api_key: str = Field(default="", alias="AMAP_API_KEY")

    # 和风天气
    qweather_api_key: str = Field(default="", alias="QWEATHER_API_KEY")
    qweather_api_host: str = Field(default="https://devapi.qweather.com", alias="QWEATHER_API_HOST")

    # PostgreSQL / pgvector
    database_url: str = Field(
        default="postgresql+psycopg://travelmind:travelmind@localhost:5432/travelmind",
        alias="DATABASE_URL",
    )
    vector_dim: int = Field(default=1536, alias="VECTOR_DIM")

    # 运行模式：未配置外部 API Key 时回退到 Mock
    enable_mock: bool = Field(default=True, alias="ENABLE_MOCK")

    # 行程约束常量（可被环境变量覆盖）
    max_one_way_drive_minutes: int = 150
    default_latest_return: str = "21:00"
    lunch_minutes: int = 60
    time_buffer_ratio: float = 0.20
    max_revision_iterations: int = 3

    # 多 Agent 工作流开关与路由上限
    multi_agent_enabled: bool = Field(default=True, alias="MULTI_AGENT_ENABLED")
    max_research_iterations: int = Field(default=2, alias="MAX_RESEARCH_ITERATIONS")
    max_planning_iterations: int = Field(default=3, alias="MAX_PLANNING_ITERATIONS")
    enable_llm_critic: bool = Field(default=True, alias="ENABLE_LLM_CRITIC")

    # ---- 认证 / 会话持久化 ----
    # AUTH_SECRET 必须通过环境变量注入；默认值仅用于本地开发，启动时会打印告警。
    auth_secret: str = Field(default="dev-insecure-secret-change-me", alias="AUTH_SECRET")
    cookie_name: str = Field(default="tm_session", alias="AUTH_COOKIE_NAME")
    cookie_secure: bool = Field(default=False, alias="COOKIE_SECURE")
    cookie_samesite: str = Field(default="lax", alias="COOKIE_SAMESITE")
    session_ttl_seconds: int = Field(default=7 * 24 * 3600, alias="SESSION_TTL_SECONDS")
    # auto: 优先 PostgreSQL，不可用时降级内存（多进程/重启不保证持久化）
    storage_backend: str = Field(default="auto", alias="STORAGE_BACKEND")
    auto_create_tables: bool = Field(default=True, alias="AUTO_CREATE_TABLES")
    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173", alias="CORS_ORIGINS"
    )

    # ---- 短期记忆 / 长期记忆 ----
    recent_message_limit: int = Field(default=8, alias="RECENT_MESSAGE_LIMIT")
    enable_llm_summary: bool = Field(default=False, alias="ENABLE_LLM_SUMMARY")

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key)

    @property
    def embedding_enabled(self) -> bool:
        return bool(self.llm_api_key)

    @property
    def amap_enabled(self) -> bool:
        return bool(self.amap_api_key)

    @property
    def qweather_enabled(self) -> bool:
        return bool(self.qweather_api_key)

    @property
    def use_mock(self) -> bool:
        """外部服务未配置时自动进入 Mock 模式。"""
        return self.enable_mock and not (
            self.llm_enabled and self.amap_enabled and self.qweather_enabled
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def auth_secret_is_default(self) -> bool:
        return self.auth_secret == "dev-insecure-secret-change-me"


@lru_cache
def get_settings() -> Settings:
    return Settings()
