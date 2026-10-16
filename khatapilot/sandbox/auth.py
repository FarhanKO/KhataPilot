"""Guard-to-provider authorization protocol, version 1 (the provider's side).

Every side-effecting request carries two headers:

    Idempotency-Key:        a unique key chosen by the caller
    X-Guard-Authorization:  v1.<payload>.<signature>

    payload    base64url (no padding) of canonical JSON with the claims
               tid  token id            kind  action kind        ph   params hash
               idk  idempotency key     tier  permission tier    did  guard decision id
               iat  issued at (unix s)  exp   expiry (unix s)
    signature  hex HMAC-SHA256(GUARD_SIGNING_KEY, "v1." + payload)

The params hash is the SHA-256 of the canonical JSON of exactly the fields in HASH_FIELDS[kind],
computed by the provider from the request body it actually received. Canonical JSON means sorted
keys, no whitespace, UTF-8 without ASCII escaping.

So a request is accepted only if the guard approved *this* account, *this* payee, *this* amount and
*this* idempotency key, recently. The backend implements the same spec in `app.core.actions`; a
contract test checks the two produce identical hashes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any

PROTOCOL_VERSION = "v1"

HASH_FIELDS: dict[str, tuple[str, ...]] = {
    "payment.execute": ("from_account", "to_number", "amount_paisa", "method"),
    "message.send": ("from_account", "to_number", "channel", "body_sha256"),
    "loan.submit": ("applicant_account", "product_id", "amount_paisa", "tenure_months", "payload_sha256"),
    "account.freeze": ("account_id",),
    "account.unfreeze": ("account_id",),
}


class AuthError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class VerifiedAuthorization:
    token_id: str
    kind: str
    params_hash: str
    idempotency_key: str
    tier: int
    decision_id: str | None
    issued_at: int
    expires_at: int


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_hex(data: bytes | str) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def params_hash(kind: str, params: dict[str, Any]) -> str:
    if kind not in HASH_FIELDS:
        raise AuthError("UNKNOWN_KIND", f"Unknown action kind {kind!r}.")
    missing = [field for field in HASH_FIELDS[kind] if field not in params]
    if missing:
        raise AuthError("MALFORMED_REQUEST", f"Missing fields for {kind}: {missing}.")
    return sha256_hex(canonical_json({field: params[field] for field in HASH_FIELDS[kind]}))


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _signature(key: bytes, payload: str) -> str:
    return hmac.new(key, f"{PROTOCOL_VERSION}.{payload}".encode("ascii"), hashlib.sha256).hexdigest()


def encode_token(key: bytes, claims: dict[str, Any]) -> str:
    """Reference signer, used by the sandbox's own tests. The backend signs in `app.guard.capability`."""
    payload = _b64encode(canonical_json(claims))
    return f"{PROTOCOL_VERSION}.{payload}.{_signature(key, payload)}"


def verify(
    header: str | None,
    *,
    key: bytes,
    kind: str,
    params: dict[str, Any],
    idempotency_key: str | None,
    now: float,
    skew_seconds: int = 30,
) -> VerifiedAuthorization:
    """Check signature, kind, params hash, idempotency key and issue time. Expiry is checked
    separately (`check_not_expired`) so an idempotent replay of a finished request still works."""
    if not header:
        raise AuthError("MISSING_AUTHORIZATION", "No guard authorization on this request.")
    parts = header.split(".")
    if len(parts) != 3 or parts[0] != PROTOCOL_VERSION:
        raise AuthError("MALFORMED_AUTHORIZATION", "The guard authorization header is malformed.")
    if not hmac.compare_digest(_signature(key, parts[1]), parts[2]):
        raise AuthError("BAD_SIGNATURE", "The guard signature does not verify.")
    try:
        claims = json.loads(_b64decode(parts[1]))
    except (ValueError, json.JSONDecodeError) as exc:
        raise AuthError("MALFORMED_AUTHORIZATION", "The guard authorization payload is unreadable.") from exc
    if claims.get("kind") != kind:
        raise AuthError("WRONG_KIND", f"Authorization is for {claims.get('kind')!r}, not {kind!r}.")
    if claims.get("ph") != params_hash(kind, params):
        raise AuthError(
            "PARAMS_MISMATCH",
            "The request does not match what the guard authorized (account, payee, amount or content changed).",
        )
    if not idempotency_key or claims.get("idk") != idempotency_key:
        raise AuthError("IDEMPOTENCY_KEY_MISMATCH", "The idempotency key does not match the authorization.")
    if int(claims.get("iat", 0)) > now + skew_seconds:
        raise AuthError("NOT_YET_VALID", "The authorization was issued in the future.")
    return VerifiedAuthorization(
        token_id=str(claims["tid"]),
        kind=kind,
        params_hash=claims["ph"],
        idempotency_key=idempotency_key,
        tier=int(claims.get("tier", -1)),
        decision_id=claims.get("did"),
        issued_at=int(claims["iat"]),
        expires_at=int(claims["exp"]),
    )


def check_not_expired(auth: VerifiedAuthorization, now: float) -> None:
    if now > auth.expires_at:
        raise AuthError("EXPIRED", "The guard authorization has expired.")
