"""
app/core/config.py
Central settings — loaded once, shared everywhere.
"""
from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── App ──────────────────────────────────────────────────────────────────
    app_env: str = "development"
    app_secret_key: str = "change-me"
    cors_origins: str = "http://localhost:5173,http://localhost:3000"
    log_level: str = "INFO"

    # ── Groq ─────────────────────────────────────────────────────────────────
    groq_api_key: str = ""

    # ── Supabase ─────────────────────────────────────────────────────────────
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""

    # ── The Odds API ──────────────────────────────────────────────────────────
    odds_api_key: str = ""
    odds_api_base_url: str = "https://api.the-odds-api.com/v4"

    # ── Reddit ────────────────────────────────────────────────────────────────
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "OracleX/1.0"

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = ""

    # ── Scheduler intervals ───────────────────────────────────────────────────
    odds_fetch_interval_minutes: int = 10
    sentiment_fetch_interval_minutes: int = 30
    prediction_generate_interval_minutes: int = 60

    # ── Model routing ─────────────────────────────────────────────────────────
    groq_narrative_model: str = "llama-3.3-70b-versatile"
    groq_sentiment_model: str = "llama-3.1-8b-instant"
    groq_reasoning_model: str = "deepseek-r1-distill-llama-70b"

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",")]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
