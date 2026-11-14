"""The owner's own Android phone as an SMS gateway, through the open-source "SMS Gateway for Android" app
(https://sms-gate.app, Local Server mode). KhataPilot reaches the phone over USB with `adb forward`, so it
works with no internet and no cloud account:

    adb forward tcp:8080 tcp:8080   # this machine's 127.0.0.1:8080 is the phone's API
    adb reverse tcp:8000 tcp:8000   # the phone's 127.0.0.1:8000 is this backend (for the webhook)

What it does for KhataPilot: sends SMS from the owner's own SIM (`POST /message`), reads SMS already on the
phone (`GET /inbox`) and tells the phone where to deliver new ones (`POST /webhooks`, event `sms:received`).
The phone signs each webhook with HMAC-SHA256 over the raw body followed by the X-Timestamp value.

Only the guard decides whether a message may be sent; this class only carries it.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime
from typing import Any

import httpx

WEBHOOK_ID = "khatapilot-sms"
MAX_SKEW_SECONDS = 300


class PhoneGatewayError(Exception):
    pass


def verify_webhook(body: bytes, signature: str | None, timestamp: str | None, key: str, *,
                   now: float | None = None) -> str | None:
    """None when the phone's signature is good and recent, else why it is not."""
    if not key:
        return "no PHONE_WEBHOOK_KEY is set"
    if not signature or not timestamp:
        return "the request is not signed"
    try:
        sent = int(timestamp)
    except ValueError:
        return "the timestamp is not a number"
    if abs((now if now is not None else time.time()) - sent) > MAX_SKEW_SECONDS:
        return "the signature is too old (or the phone's clock is wrong)"
    expected = hmac.new(key.encode(), body + timestamp.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature.strip().lower()):
        return "the signature does not match"
    return None


class PhoneGateway:
    def __init__(self, base_url: str, *, username: str, password: str, timeout: float = 8.0,
                 http: httpx.Client | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = http or httpx.Client(base_url=self.base_url, timeout=timeout, auth=(username, password))

    def close(self) -> None:
        self._http.close()

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise PhoneGatewayError(f"the phone did not answer ({exc.__class__.__name__}); is it connected "
                                    "and the SMS Gateway app running?") from exc
        if response.status_code == 401:
            raise PhoneGatewayError("the phone refused the username or password (PHONE_GATEWAY_USER/PASSWORD)")
        if response.status_code >= 400:
            raise PhoneGatewayError(f"the phone answered {response.status_code}: {response.text[:200]}")
        try:
            return response.json() if response.content else {}
        except ValueError:
            return {}

    def health(self) -> dict:
        return self._call("GET", "/health")

    def send_sms(self, numbers: list[str], text: str) -> dict:
        """Hand one SMS to the phone, which sends it from the owner's SIM. Returns the phone's message state."""
        return self._call("POST", "/message", json={"textMessage": {"text": text}, "phoneNumbers": numbers,
                                                     "withDeliveryReport": True})

    def inbox(self, *, since: datetime | None = None, limit: int = 200) -> list[dict]:
        params: dict[str, Any] = {"type": "SMS", "limit": max(1, min(limit, 500))}
        if since is not None:
            params["from"] = since.isoformat()
        found = self._call("GET", "/inbox", params=params)
        return found if isinstance(found, list) else list(found.get("messages", [])) if isinstance(found, dict) else []

    def register_webhook(self, url: str) -> dict:
        return self._call("POST", "/webhooks", json={"id": WEBHOOK_ID, "url": url, "event": "sms:received"})
