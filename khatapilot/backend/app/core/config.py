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
    # With a real model: short, plain owner requests the offline planner reads for certain (balances,
    # who owes, "sold rice ৳1,500 cash") are answered by it, at no API cost; everything else goes to the model.
    llm_rules_first: bool = True
    # With a real model: when it cannot be reached (network, timeout, busy, down), the offline planner
    # answers that turn instead, marked "offline" in the chat and the trace. A bad key still shows its error.
    llm_offline_fallback: bool = True
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
    # Run that loop inside the web server (the simple way), or turn this off and run it in its own process
    # (`python scripts/worker.py`), so a busy or restarting web server never delays a payment or an alert. A
    # lease in the database makes sure only one process runs the jobs either way.
    scheduler_in_web: bool = True
    # Logs: one JSON object per line on stderr and in data_dir/logs (rotated), `text` for a terminal. Errors are
    # grouped in data_dir/logs/errors.json and counted in /api/metrics; with SENTRY_DSN (and sentry-sdk
    # installed) they also go to Sentry.
    log_format: Literal["json", "text"] = "json"
    log_level: str = "INFO"
    sentry_dsn: str = ""
    # Requests per client per minute (0 disables a bucket): routes that take the PIN, chat messages (each can
    # call the model), and everything else. STOP is never limited.
    rate_limit_pin_per_minute: int = 20
    rate_limit_chat_per_minute: int = 60
    rate_limit_other_per_minute: int = 600
    # Backups of the books and the audit log (python scripts/backup.py). With `backup_daily` the backend also
    # makes one a day itself; the newest `backup_keep` are kept (copies made before a restore always are).
    backup_dir: Path = REPO_ROOT / "var" / "backups"
    backup_daily: bool = False
    backup_keep: int = 14
    # AI spend per shop per calendar month, in US$ at list prices (0 = no cap). When it is reached, the
    # offline planner answers every turn until the month ends; the owner sees the month's use in Alerts.
    llm_monthly_budget_usd: float = 2.0
    usd_to_bdt: float = 122.0  # only to show the owner the spend in taka, "about ৳…"
    # /api/metrics (Prometheus text). With a token set, a scraper sends it as a bearer token; without one,
    # only this machine (loopback) can read the metrics.
    metrics_token: SecretStr = SecretStr("")
    # The owner's own Android phone, through the "SMS Gateway for Android" app in Local Server mode
    # (app/infra/phone_gateway.py): it hands KhataPilot the bKash, Nagad and bank SMS it receives, and can send
    # SMS from the owner's SIM. Over USB: `adb forward tcp:8080 tcp:8080` and `adb reverse tcp:8000 tcp:8000`.
    phone_gateway_url: str = ""  # e.g. http://127.0.0.1:8080 (empty: no phone)
    phone_gateway_user: str = ""
    phone_gateway_password: SecretStr = SecretStr("")
    phone_webhook_key: SecretStr = SecretStr("")  # the app's Settings > Webhooks > Signing Key
    phone_webhook_url: str = "http://127.0.0.1:8000/api/phone/webhook"  # this backend, as the phone reaches it
    # A money SMS from the provider's own sender ID ("bKash", "NAGAD", a bank) on the owner's phone: "sender_id"
    # books it even when the provider cannot confirm the TrxID (there is no statement API for a real personal
    # account); "provider" books only what the provider confirms. Above `phone_sms_review_taka`, an unconfirmed
    # SMS always waits for the owner. A money SMS from an ordinary number is never booked.
    phone_sms_trust: Literal["sender_id", "provider"] = "sender_id"
    phone_sms_review_taka: int = 20_000
    # Every SMS KhataPilot sends (reminders, the owner's alerts) passes the guard and the simulated gateway's
    # signature check; with `sms_via_phone` it then also leaves from the owner's phone as a real SMS.
    sms_via_phone: bool = False
    owner_phone: str = ""  # the owner's own number, for alerts by SMS
    owner_alert_sms: bool = False  # urgent and important alerts also go to owner_phone by SMS
    owner_alert_sms_per_day: int = 6

    # The advisor: once a week (local time, on this weekday, 0 = Monday ... 5 = Saturday, from this hour) its
    # advice lands in Alerts, written by the model from the books' facts (checked number by number) or, offline
    # or over the AI budget, in fixed words. The owner can also ask for advice in chat at any time.
    advice_weekly: bool = True
    advice_weekday: int = 5
    advice_hour: int = 9

    # Voice input: speech is turned into text on this machine by an offline Whisper model (faster-whisper,
    # `pip install -r requirements-voice.txt`, `python scripts/get_voice_model.py`), primed with the shop's own
    # customer and supplier names and Banglish words. Without it, the browser's own recognizer is used.
    stt_model: str = "large-v3-turbo"  # or small / medium for slower machines
    stt_model_dir: Path = REPO_ROOT / "var" / "models" / "whisper"
    stt_threads: int = 0  # 0: as many as the CPU has
    stt_enabled: bool = True

    # Cash forecast: the standard safety buffer, used until the books have two weeks of sales to size one
    # from the shop (or the owner sets their own in chat), and how far ahead to look.
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

    @field_validator("data_dir", "risk_model_path", "credit_model_path", "backup_dir", "stt_model_dir")
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
