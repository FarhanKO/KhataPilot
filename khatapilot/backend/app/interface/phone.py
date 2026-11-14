"""The owner's phone as a channel: SMS fetched from it are booked the way a forwarded SMS is.

The phone (the SMS Gateway for Android app) delivers each new SMS to `POST /api/phone/webhook`, signed with
its HMAC key; the owner can also pull what is already on the phone, import an "SMS Backup & Restore" file,
or share a single SMS into the app. Whatever the route, every SMS goes through `ingest`:

1. Not about money (a chat, an OTP, an offer): counted and dropped. It is never stored.
2. A money SMS from an ordinary mobile number ("You have received Tk 5,000..." from 017...): the commonest
   scam against shopkeepers. It is never booked; the owner gets a security alert.
3. A money SMS from the provider's own sender ID ("bKash", "NAGAD", a bank): handed to the guard's
   `record_sms` tool in a turn from "Your phone (SMS)", the SMS itself an untrusted segment labelled
   `phone_sms`. The guard reads it with the strict parser, refuses a TrxID that is already in the books,
   asks the owner when the money comes from a customer who owes baki, and books it. If the provider (the
   sandbox) cannot confirm the TrxID, it is still booked when `PHONE_SMS_TRUST=sender_id` and the amount is
   at most `PHONE_SMS_REVIEW_TAKA`; otherwise it waits for the owner's Book or Ignore.
4. A money SMS from an unknown sender (a pasted text with no sender): like an SMS forwarded in chat, it is
   booked only if the provider confirms it.

Every SMS is remembered by the hash of its sender and text, so the webhook and a later pull never book the
same SMS twice (and the TrxID check stops a re-worded copy).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError

from app.core.ids import new_id
from app.core.masking import mask_phone, mask_text, normalize_msisdn
from app.core.money import fmt_taka, normalize_digits
from app.core.provenance import Provenance, Segment, Sender, SenderKind, TurnContext
from app.core.sms import SMS_START_RE, is_personal_number, parse_sms, sender_provider
from app.guard.gateway import ToolCall
from app.guard.service import Guard
from app.modules.inbox.schemas import IncomingSms
from app.modules.inbox.service import body_hash
from app.modules.reminders.service import reply_choice

PHONE_SENDER = Sender(kind=SenderKind.OWNER, display="Your phone (SMS)")
MAX_BATCH = 500
# Money words a fake notification uses even when it does not parse (no TrxID, odd spacing).
MONEY_TEXT = re.compile(r"(?:received|cash\s*in|payment|credited|deposit(?:ed)?)\b.{0,40}?(?:tk\.?|bdt|৳)\s*[\d,]+",
                        re.I | re.S)
PROVIDER_NAMES = {"bkash": "bKash", "nagad": "Nagad", "bank": "bank"}


def _sender_label(sender: str) -> tuple[str, str]:
    """(what to store and show, its kind). Personal numbers are masked."""
    if is_personal_number(sender):
        return mask_phone(normalize_msisdn(sender) or sender), "personal"
    if sender_provider(sender):
        return sender.strip()[:40], "provider"
    return (sender.strip()[:40] or "unknown"), "other"


def ingest(container, messages: list[IncomingSms]) -> dict:
    """Book what can be booked; returns how many SMS ended up in each state."""
    counts: Counter[str] = Counter()
    for sms in messages[:MAX_BATCH]:
        try:
            with container.db.session() as session:
                counts[_one(Guard(session, container.guard_deps), sms)] += 1
        except IntegrityError:  # the same SMS arrived twice at once (webhook and pull): the first one won
            counts["already_seen"] += 1
    counts["received"] = min(len(messages), MAX_BATCH)
    return dict(counts)


def _one(guard: Guard, sms: IncomingSms) -> str:
    body = sms.body.strip()
    if not body:
        return "skipped"
    digest = body_hash(sms.sender, body)
    if guard.inbox.seen(digest):
        return "already_seen"
    received = sms.received_at.astimezone(UTC) if sms.received_at and sms.received_at.tzinfo else sms.received_at
    parsed = parse_sms(body)
    shown, kind = _sender_label(sms.sender)
    money_like = parsed is not None or bool(MONEY_TEXT.search(normalize_digits(body))) or \
        bool(SMS_START_RE.search(normalize_digits(body)))
    if not money_like:
        if kind == "personal" and (choice := reply_choice(body)):
            return _customer_reply(guard, sms.sender, body, choice, received)
        guard.inbox.skip(received)
        return "skipped"
    if kind == "personal":
        guard.inbox.add(digest=digest, sender=shown, sender_kind=kind, source=sms.source, body=mask_text(body),
                              received_at=received, sms=parsed, status="fake_sender",
                              detail="A payment SMS from an ordinary mobile number, not from the provider. Not booked.")
        what = (f"says you received {fmt_taka(parsed.amount_paisa)} (TrxID {parsed.trx_id})" if parsed
                else "looks like a payment notification")
        provider = PROVIDER_NAMES.get(parsed.provider, "bKash") if parsed else "bKash"
        guard.recorder.alert(
            request_id=new_id("phone"), channel="phone", summary=f"Possible fake {provider} SMS from {shown}",
            reason=(f"An SMS from {shown} {what}. Real {provider} messages come from \"{provider}\", not from a "
                    f"phone number, so KhataPilot did not book it. Check your {provider} app before you hand over "
                    "goods or change anyone's baki."))
        return "fake_sender"
    if parsed is None:
        guard.inbox.skip(received)  # a provider's own message we cannot read for certain (an offer, an OTP)
        return "skipped"
    row = guard.inbox.add(digest=digest, sender=shown, sender_kind=kind, source=sms.source, body=body,
                          received_at=received, sms=parsed, status="not_booked")
    provider = sender_provider(sms.sender)
    if kind == "provider" and provider != parsed.provider:
        why = (f"Sent by {shown}, but it reads like a {PROVIDER_NAMES[parsed.provider]} SMS. Check it, then Book "
               "or Ignore.")
        guard.inbox.settle(row, "review", why)
        _tell_review(guard, row, why)
        return "review"
    label = "phone_sms" if kind == "provider" else "forwarded_sms"
    return _submit(guard, row, label, channel="phone", sender=PHONE_SENDER)


def _customer_reply(guard: Guard, sender: str, text: str, choice: str, received) -> str:
    """A customer answered a reminder by SMS with STOP or START: recorded without the model (only short,
    unmistakable replies count, see reminders.reply_choice). The SMS itself is not kept."""
    number = normalize_msisdn(sender)
    guard.inbox.skip(received)
    if number is None:
        return "skipped"
    who = guard.reminders.name_for(number) or mask_phone(number)
    if choice == "stop":
        guard.reminders.opt_out(number, source="customer", words=text)
        guard.recorder.notify(request_id=new_id("phone"), channel="phone", summary=f"No more reminders to {who}",
                              reason=(f"{who} replied \"{text.strip()[:60]}\" by SMS. KhataPilot will not prepare or "
                                      "send them baki reminders; their baki stays in the books. Only they can turn "
                                      "reminders back on, by replying START."))
        return "stopped"
    if not guard.reminders.opt_in(number, source="customer"):
        return "skipped"
    guard.recorder.notify(request_id=new_id("phone"), channel="phone", summary=f"Reminders back on for {who}",
                          reason=f"{who} replied \"{text.strip()[:60]}\" by SMS and asked for baki reminders again. "
                                 "Each one still waits for your approval.")
    return "started"


def _submit(guard: Guard, row, label: str, *, channel: str, sender: Sender) -> str:
    turn = TurnContext(request_id=new_id("req"), conversation_id="phone-sms", shop_id=guard.shop_id, channel=channel,
                       sender=sender, segments=(Segment(text=row.body, provenance=Provenance.UNTRUSTED, source=label),),
                       received_at=guard.clock.now())
    outcome = guard.gateway.submit(ToolCall(name="record_sms", arguments={"segment": 1}), turn)
    data = outcome.data if isinstance(outcome.data, dict) else {}
    if outcome.status == "ok":
        still = f" They still owe {data['customer_still_owes']}." if data.get("customer_still_owes") not in (None, "৳0") \
            else ""
        guard.inbox.settle(row, "booked", f"Booked as {data.get('recorded', 'an entry')}, {data.get('amount', '')}."
                           + still, decision_id=outcome.decision_id, resolved=True)
        return "booked"
    if outcome.status == "asked" and data.get("question_id"):
        guard.inbox.settle(row, "asked", outcome.message, decision_id=outcome.decision_id,
                           question_id=data["question_id"])
        return "asked"
    if outcome.status == "asked":
        guard.inbox.settle(row, "review", outcome.message, decision_id=outcome.decision_id)
        _tell_review(guard, row, outcome.message)
        return "review"
    if "already in your books" in outcome.message:
        guard.inbox.settle(row, "duplicate", outcome.message, decision_id=outcome.decision_id, resolved=True)
        return "duplicate"
    guard.inbox.settle(row, "not_booked", outcome.message, decision_id=outcome.decision_id, resolved=True)
    return "not_booked"


def _tell_review(guard: Guard, row, why: str) -> None:
    guard.recorder.alert(request_id=new_id("phone"), channel="phone",
                         summary=f"Phone SMS waits for you: {fmt_taka(row.amount_paisa or 0)} (TrxID {row.trx_id})",
                         reason=why + " It is in the Ledger tab under Phone SMS.")


def book_checked(guard: Guard, row) -> str:
    """The owner looked at a waiting SMS in the app and tapped Book: booked unless the provider contradicts it."""
    owner = Sender(kind=SenderKind.OWNER, display="Owner (web)")
    return _submit(guard, row, "phone_sms_checked", channel="web", sender=owner)


# ---- other ways in: the phone's own formats and imports

def _when(value) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            number = float(value)
            return datetime.fromtimestamp(number / 1000 if number > 1e11 else number, UTC)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, OverflowError, OSError):
        return None


def from_webhook(event: dict) -> list[IncomingSms]:
    """SMS Gateway for Android webhook: {"event": "sms:received", "payload": {"message", "sender", "receivedAt"}}
    (older versions say "phoneNumber"); batch events carry a list."""
    if not isinstance(event, dict) or not str(event.get("event", "")).startswith("sms:") \
            or "received" not in str(event.get("event", "")):
        return []
    payload = event.get("payload")
    items = payload if isinstance(payload, list) else (payload or {}).get("messages", [payload]) \
        if isinstance(payload, dict) else []
    return [IncomingSms(sender=str(p.get("sender") or p.get("phoneNumber") or "")[:64],
                        body=str(p.get("message") or p.get("text") or "")[:2_000],
                        received_at=_when(p.get("receivedAt")), source="webhook")
            for p in items if isinstance(p, dict) and (p.get("message") or p.get("text"))]


def from_inbox(items: list[dict]) -> list[IncomingSms]:
    """`GET /inbox` on the phone."""
    found = []
    for item in items:
        if not isinstance(item, dict) or str(item.get("type", "SMS")).upper() != "SMS":
            continue
        text = item.get("message") or item.get("text") or item.get("content") or item.get("contentPreview")
        if text:
            found.append(IncomingSms(sender=str(item.get("sender") or item.get("phoneNumber") or "")[:64],
                                     body=str(text)[:2_000], received_at=_when(item.get("createdAt")
                                                                               or item.get("receivedAt")),
                                     source="pull"))
    return found


def from_backup_xml(xml: str) -> list[IncomingSms]:
    """An "SMS Backup & Restore" file: <smses><sms address="bKash" body="..." date="1697..." type="1"/></smses>.
    Only received SMS (type 1)."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError(f"That file is not an SMS backup the app can read ({exc}).") from None
    return [IncomingSms(sender=(node.get("address") or "")[:64], body=(node.get("body") or "")[:2_000],
                        received_at=_when(node.get("date")), source="import")
            for node in root.iter("sms") if node.get("type", "1") == "1" and node.get("body")]


def from_text(text: str) -> list[IncomingSms]:
    """Pasted SMS, one after another: each starts where a bKash/Nagad/bank notification starts."""
    clean = text.strip()
    starts = [m.start() for m in SMS_START_RE.finditer(normalize_digits(clean))]
    if not starts:
        return [IncomingSms(body=clean[:2_000], source="import")] if clean else []
    bounds = [*starts, len(clean)]
    return [IncomingSms(body=clean[a:b].strip()[:2_000], source="import") for a, b in zip(bounds, bounds[1:])
            if clean[a:b].strip()]
