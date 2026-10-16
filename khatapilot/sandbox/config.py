"""Sandbox settings. The sandbox plays the provider, so it holds its own copy of the shared
signing key and uses it only to *verify* the guard's signatures."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[1]
MIN_SECRET_LENGTH = 32


class SandboxSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    sandbox_data_dir: Path = REPO_ROOT / "sandbox" / "var"
    guard_signing_key: SecretStr = SecretStr("")
    sandbox_admin_key: SecretStr = SecretStr("")
    sim_time: datetime | None = None
    token_clock_skew_seconds: int = 30

    def check_secrets(self) -> None:
        for name in ("guard_signing_key", "sandbox_admin_key"):
            if len(getattr(self, name).get_secret_value()) < MIN_SECRET_LENGTH:
                raise RuntimeError(
                    f"{name.upper()} is missing or shorter than {MIN_SECRET_LENGTH} characters. "
                    "Run `python scripts/make_env.py` to create .env with fresh secrets."
                )
