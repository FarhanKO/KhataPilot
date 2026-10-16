"""Application settings, read from environment variables and the repo-root `.env` file.

The guard's signing key is deliberately *not* here: only `app.guard.capability` reads it,
so nothing else in the process (the agent layer in particular) can get at it.
"""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]

LlmMode = Literal["mock", "anthropic", "openai_compatible"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["dev", "test", "demo"] = "dev"
    data_dir: Path = REPO_ROOT / "var"
    database_url: str = ""
    audit_database_url: str = ""
    sandbox_url: str = "http://127.0.0.1:8100"
    sandbox_admin_key: SecretStr = SecretStr("")
    owner_token: SecretStr = SecretStr("")
    frontend_origin: str = "http://localhost:5173"

    # LLM provider (see app/agent/llm). `mock` needs no key and no network.
    llm_mode: LlmMode = "mock"
    llm_model: str = "claude-sonnet-5-5"
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    llm_fallbacks: bool = True  # server-side refusal fallback on the Claude API (supported models only)
    llm_timeout_seconds: float = 60.0
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    openai_base_url: str | None = None
    openai_model: str = "gpt-4o-mini"

    # Fraud scoring: the trained Fraud Sentinel model, or the transparent rules if it is missing.
    risk_model: Literal["sentinel", "rules"] = "sentinel"
    risk_model_path: Path = REPO_ROOT / "backend" / "models_store" / "fraud_sentinel.joblib"
    # Credit profile: the trained model (scripts/train_credit.py).
    credit_model_path: Path = REPO_ROOT / "backend" / "models_store" / "credit_model.joblib"
    # How often scheduled payments are checked and run (0 disables the background loop).
    scheduler_interval_seconds: int = 60
    # Cash forecast: the safety buffer the owner wants to keep on hand, and how far ahead to look.
    forecast_buffer_taka: int = 5_000
    forecast_days: int = 14

    # The shop this backend serves, and the simulated "now" for demos and tests.
    shop_id: str = "rahman-store"
    sim_time: datetime | None = None

    # Guard defaults. The owner can change limits later (PIN-protected); these seed the policy store.
    per_txn_limit_taka: int = 10_000
    daily_limit_taka: int = 25_000
    fraud_medium: float = 0.50
    fraud_high: float = 0.85
    pin_max_attempts: int = 3
    pin_lock_minutes: int = 15
    split_window_hours: int = 24
    velocity_max_per_hour: int = 5
    token_ttl_seconds: int = 300
    intent_ttl_minutes: int = 30
    agent_max_steps: int = 6

    @field_validator("data_dir", "risk_model_path", "credit_model_path")
    @classmethod
    def _absolute_paths(cls, v: Path) -> Path:
        return v if v.is_absolute() else (REPO_ROOT / v)

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{(self.data_dir / 'khatapilot.db').as_posix()}"

    @property
    def audit_db_url(self) -> str:
        return self.audit_database_url or f"sqlite:///{(self.data_dir / 'audit.db').as_posix()}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
