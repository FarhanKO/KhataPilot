"""Request dependencies: the container and owner authentication."""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, Request

from app.core.provenance import Sender, SenderKind

MIN_TOKEN_LENGTH = 16
OWNER = Sender(kind=SenderKind.OWNER, display="Owner (web)")


def get_container(request: Request):
    return request.app.state.container


def require_owner(request: Request, authorization: str | None = Header(default=None)) -> Sender:
    """The local web demo authenticates the owner with a bearer token from .env. (Messenger would bind the
    owner's PSID and device instead.) Everything that reaches the agent through this check is the owner."""
    expected = request.app.state.settings.owner_token.get_secret_value()
    if len(expected) < MIN_TOKEN_LENGTH:
        raise HTTPException(503, "OWNER_TOKEN is not configured. Run `python scripts/make_env.py`.")
    supplied = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not supplied or not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(401, "Owner authentication required.")
    return OWNER
