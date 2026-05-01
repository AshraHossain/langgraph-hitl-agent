"""Centralised configuration via pydantic-settings."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    llm_model: str = Field(default="gpt-4o-mini", alias="LLM_MODEL")
    llm_temperature: float = Field(default=0.1, alias="LLM_TEMPERATURE")

    # LangSmith
    langchain_tracing_v2: bool = Field(default=False, alias="LANGCHAIN_TRACING_V2")
    langchain_api_key: str = Field(default="", alias="LANGCHAIN_API_KEY")
    langchain_project: str = Field(default="langgraph-hitl-agent", alias="LANGCHAIN_PROJECT")

    # Postgres
    postgres_host: str = Field(default="localhost", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_db: str = Field(default="hitl", alias="POSTGRES_DB")
    postgres_user: str = Field(default="hitl", alias="POSTGRES_USER")
    postgres_password: str = Field(default="hitl", alias="POSTGRES_PASSWORD")

    # Redis
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    # API
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Retry budget
    retry_budget_per_thread: int = Field(default=5, alias="RETRY_BUDGET_PER_THREAD")
    retry_budget_window_seconds: int = Field(default=300, alias="RETRY_BUDGET_WINDOW_SECONDS")

    @property
    def pg_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def pg_async_dsn(self) -> str:
        # asyncpg does not accept the +asyncpg driver suffix
        return self.pg_dsn


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
