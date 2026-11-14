"""The owner's alerts, sorted into segments (read-only; see app.interface.notifications)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.provenance import Sender
from app.interface import notifications
from app.interface.deps import get_container, require_owner

router = APIRouter(tags=["notifications"])


@router.get("/notifications")
def feed(scan: int = Query(default=600, ge=1, le=2000, description="How many recent audit entries to look through."),
         container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> dict:
    """`chain_id` is the hash of the log's first entry: it changes when the demo shop is reloaded, so a
    client can forget what it had marked read."""
    entries = container.audit.entries(limit=scan, newest_first=True)
    first = container.audit.entries(limit=1)
    return notifications.build(entries, chain_id=first[0].hash if first else "empty")
