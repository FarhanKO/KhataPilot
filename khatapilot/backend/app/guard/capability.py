"""Mints the guard's signed authorizations. This is the only code in the backend that reads
GUARD_SIGNING_KEY (an architecture test enforces it).

An `AuthorizedAction` binds one side effect: the provider action kind, a SHA-256 hash of exactly
the fields that move money or data, the idempotency key, the tier and the guard decision. It is
signed with HMAC-SHA256 and expires after a few minutes. The provider verifies it independently
(protocol v1, described in `sandbox/auth.py`).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from collections.abc import Callable
from typing import Any

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.actions import AuthorizedAction, canonical_json, params_hash
from app.core.config import REPO_ROOT
from app.core.ids import new_id

PROTOCOL_VERSION = "v1"
MIN_KEY_LENGTH = 32


class _SigningKeySettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    guard_signing_key: SecretStr = SecretStr("")


def load_signing_key() -> bytes:
    return _SigningKeySettings().guard_signing_key.get_secret_value().encode("utf-8")


class CapabilitySigner:
    def __init__(self, key: bytes | None = None, *, ttl_seconds: int = 300,
                 wall_clock: Callable[[], float] = time.time) -> None:
        self._key = load_signing_key() if key is None else key
        if len(self._key) < MIN_KEY_LENGTH:
            raise RuntimeError("GUARD_SIGNING_KEY is missing or too short. Run `python scripts/make_env.py`.")
        self._ttl = ttl_seconds
        self._now = wall_clock

    def mint(self, *, kind: str, params: dict[str, Any], idempotency_key: str, tier: int,
             decision_id: str) -> AuthorizedAction:
        issued = int(self._now())
        token_id = new_id("tok")
        claims = {
            "tid": token_id,
            "kind": kind,
            "ph": params_hash(kind, params),
            "idk": idempotency_key,
            "tier": tier,
            "did": decision_id,
            "iat": issued,
            "exp": issued + self._ttl,
        }
        payload = base64.urlsafe_b64encode(canonical_json(claims)).rstrip(b"=").decode("ascii")
        signature = hmac.new(self._key, f"{PROTOCOL_VERSION}.{payload}".encode("ascii"), hashlib.sha256).hexdigest()
        return AuthorizedAction(
            token_id=token_id,
            kind=kind,
            params_hash=claims["ph"],
            idempotency_key=idempotency_key,
            tier=tier,
            decision_id=decision_id,
            issued_at=issued,
            expires_at=claims["exp"],
            header=f"{PROTOCOL_VERSION}.{payload}.{signature}",
        )
