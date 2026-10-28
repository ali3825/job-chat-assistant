"""Application settings, read from environment variables (see .env.example)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    database_url: str = f"sqlite:///{ROOT / 'data' / 'app.db'}"
    jobs_file: Path = ROOT / "data" / "jobs.json"
    frontend_dist: Path = ROOT / "frontend" / "dist"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    max_upload_mb: int = 5
    max_pdf_pages: int = 20
    session_ttl_minutes: int = 120
    max_sessions: int = 2000

    # LLM (optional). provider: none | anthropic | openai (any OpenAI-compatible API)
    llm_provider: str = "none"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_base_url: str = ""
    llm_timeout_seconds: float = 15.0

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def sqlalchemy_url(self) -> str:
        url = self.database_url
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://"):]
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        return url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
