"""The owner's phone: SMS it receives are booked through the guard (see app/interface/phone.py).

- `POST /phone/webhook`: the phone delivers each new SMS here, signed with its webhook key (no owner token:
  the phone does not have one; the HMAC signature is the proof it is the owner's phone).
- `POST /phone/pull`: read what is already on the phone (and tell it where to deliver new SMS).
- `POST /phone/import`: an "SMS Backup & Restore" file, or SMS pasted as text.
- `GET /phone`: how the phone is set up, and what happened to recent SMS.
- `POST /phone/sms/{id}/book | ignore`: the owner's answer for an SMS that waits for them.
"""

from __future__ import annotations

import json
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.masking import mask_text
from app.core.provenance import Sender
from app.guard.service import Guard
from app.infra.phone_gateway import PhoneGatewayError, verify_webhook
from app.interface.deps import get_container, require_owner
from app.interface.phone import book_checked, from_backup_xml, from_inbox, from_text, from_webhook, ingest

router = APIRouter(prefix="/phone", tags=["phone"])
MAX_WEBHOOK_BYTES = 256_000
FIRST_PULL_DAYS = 30


@router.post("/webhook")
async def webhook(request: Request, container=Depends(get_container)) -> dict:
    settings = container.settings
    body = await request.body()
    if len(body) > MAX_WEBHOOK_BYTES:
        raise HTTPException(413, "Too large.")
    problem = verify_webhook(body, request.headers.get("X-Signature"), request.headers.get("X-Timestamp"),
                             settings.phone_webhook_key.get_secret_value())
    if problem:
        raise HTTPException(401, f"Not accepted: {problem}.")
    try:
        event = json.loads(body)
    except ValueError:
        raise HTTPException(400, "Not JSON.") from None
    messages = from_webhook(event)
    if not messages:
        return {"ok": True, "received": 0}  # system:ping, sms:sent and the like
    return {"ok": True, **ingest(container, messages)}


def _phone(container):
    if container.phone is None:
        raise HTTPException(409, "No phone is set up. Set PHONE_GATEWAY_URL, PHONE_GATEWAY_USER and "
                                 "PHONE_GATEWAY_PASSWORD in .env (see the Controls tab).")
    return container.phone


@router.post("/pull")
def pull(container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> dict:
    phone = _phone(container)
    with container.db.session() as session:
        state = Guard(session, container.guard_deps).inbox.state()
        newest = state.newest_received_at
    since = (newest - timedelta(days=1)) if newest else container.clock.now() - timedelta(days=FIRST_PULL_DAYS)
    webhook_note = None
    if container.settings.phone_webhook_key.get_secret_value():
        try:  # so new SMS arrive by themselves from now on
            phone.register_webhook(container.settings.phone_webhook_url)
        except PhoneGatewayError as exc:
            webhook_note = f"New SMS will not arrive by themselves yet: {exc}."
    try:
        items = phone.inbox(since=since, limit=500)
    except PhoneGatewayError as exc:
        raise HTTPException(502, f"Could not read the phone: {exc}.") from None
    report = ingest(container, from_inbox(items))
    with container.db.session() as session:
        Guard(session, container.guard_deps).inbox.pulled()
    return {**report, "since": since.isoformat(), "webhook_note": webhook_note}


class ImportRequest(BaseModel):
    xml: str | None = Field(default=None, max_length=20_000_000)
    text: str | None = Field(default=None, max_length=200_000)


@router.post("/import")
def import_sms(body: ImportRequest, container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> dict:
    try:
        messages = from_backup_xml(body.xml) if body.xml else from_text(body.text or "")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if not messages:
        raise HTTPException(422, "No SMS found in that.")
    return ingest(container, messages)


@router.get("")
def status(container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> dict:
    settings = container.settings
    with container.db.session() as session:
        inbox = Guard(session, container.guard_deps).inbox
        state, counts, recent = inbox.state(), inbox.counts(), inbox.recent(40)
        view = {
            "connected": container.phone is not None,
            "gateway_url": settings.phone_gateway_url or None,
            "webhook": bool(settings.phone_webhook_key.get_secret_value()),
            "webhook_url": settings.phone_webhook_url,
            "trust": settings.phone_sms_trust,
            "review_over_paisa": settings.phone_sms_review_taka * 100,
            "sms_via_phone": settings.sms_via_phone and container.phone is not None,
            "owner_alert_sms": settings.owner_alert_sms and bool(settings.owner_phone),
            "last_seen_at": state.last_seen_at, "last_pull_at": state.last_pull_at, "counts": counts,
            "recent": [{**r.model_dump(mode="json"), "body": mask_text(r.body)} for r in recent],
        }
    return view


def _row(guard: Guard, sms_id: str):
    row = guard.inbox.get(sms_id)
    if row is None:
        raise HTTPException(404, "No such SMS.")
    if row.status != "review":
        raise HTTPException(409, f"That SMS is already {row.status.replace('_', ' ')}.")
    return row


@router.post("/sms/{sms_id}/book")
def book(sms_id: str, container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> dict:
    with container.db.session() as session:
        guard = Guard(session, container.guard_deps)
        row = _row(guard, sms_id)
        status = book_checked(guard, row)
        return {"status": status, "detail": row.detail, "question_id": row.question_id}


@router.post("/sms/{sms_id}/ignore")
def ignore(sms_id: str, container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> dict:
    with container.db.session() as session:
        guard = Guard(session, container.guard_deps)
        row = _row(guard, sms_id)
        guard.inbox.settle(row, "ignored", "You chose not to book it.", resolved=True)
        guard.recorder.event(request_id=f"phone-{sms_id}", channel="web", actor="owner", provenance="owner",
                             event="execution", summary=f"Phone SMS {row.trx_id or ''} not booked".strip(),
                             decision="IGNORED", reason="The owner chose Ignore for an SMS that waited for them.",
                             tier=0, decision_id=row.decision_id)
        return {"status": "ignored"}
