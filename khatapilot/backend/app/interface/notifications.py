"""The owner's alerts, sorted into segments: a read-only view over the audit log.

Nothing new is recorded here. Every alert the guard, the scheduler and the daily checks already
write to the hash-chained log is classified into one of four segments (security, payments, cash and
credit, customers), given a priority, and repeats of the same thing on the same day are folded into
one card with a count. Routine entries (reads, ordinary allowed decisions) are left out, so the feed
only holds what the owner should hear about. Pure functions, no I/O.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from app.core.clock import BDT
from app.infra.audit_log import AuditEntry

SegmentId = Literal["security", "money", "cash", "customers"]
Priority = Literal["urgent", "important", "info"]
Tab = Literal["chat", "ledger", "forecast", "savings", "credit", "loans", "controls", "audit"]

SEGMENTS: tuple[dict[str, str], ...] = (
    {"id": "security", "label": "Security", "hint": "Blocked attempts, STOP, PIN, settings and AI spend"},
    {"id": "money", "label": "Payments", "hint": "Money sent, scheduled and held"},
    {"id": "cash", "label": "Cash & credit", "hint": "Shortfall warnings, savings, loans, consent"},
    {"id": "customers", "label": "Customers", "hint": "Baki to collect, reminders, messages"},
)
RANK: dict[Priority, int] = {"urgent": 0, "important": 1, "info": 2}


@dataclass(frozen=True)
class Notice:
    """One audit entry, as the owner should hear it."""

    segment: SegmentId
    priority: Priority
    title: str
    body: str
    tab: Tab = "audit"


@dataclass
class Group:
    """Repeats of one notice on one day, shown as a single card."""

    key: str
    entries: list[AuditEntry] = field(default_factory=list)  # newest first
    notices: list[Notice] = field(default_factory=list)  # one per entry

    def view(self) -> dict:
        latest, first, notice = self.entries[0], self.entries[-1], self.notices[0]
        return {
            "id": self.key,
            "segment": notice.segment,
            "priority": min((n.priority for n in self.notices), key=RANK.__getitem__),
            "title": notice.title,
            "body": notice.body,
            "tab": notice.tab,
            "count": len(self.entries),
            "seq": latest.seq,
            "seqs": [e.seq for e in self.entries],
            "ts": latest.ts,
            "first_ts": first.ts,
            "channel": latest.channel,
            "event": latest.event,
        }


def _alert(entry: AuditEntry) -> Notice:
    summary, reason = entry.summary, entry.reason
    if summary.startswith("Blocked:"):
        return Notice("security", "urgent", summary, reason, "audit")
    if summary.startswith("Possible fake "):
        return Notice("security", "urgent", summary, reason, "ledger")
    if summary.startswith("Phone SMS waits for you"):
        return Notice("money", "important", summary, reason, "ledger")
    if summary.startswith("Held scheduled payment") or summary == "Scheduled payment failed":
        return Notice("money", "urgent", summary, reason, "chat")
    if summary.startswith("Cash warning"):
        return Notice("cash", "urgent", summary, reason, "forecast")
    if summary.startswith("AI budget"):
        return Notice("security", "important", summary, reason, "chat")
    if summary == "Books and provider do not match":
        return Notice("money", "important", summary, reason, "ledger")
    if summary.startswith("This week's advice"):
        return Notice("cash", "important", summary, reason, "chat")
    if summary.startswith("Savings ready"):
        return Notice("cash", "important", summary, reason, "savings")
    if summary.startswith("Baki to collect"):
        return Notice("customers", "important", summary, reason, "ledger")
    if summary == "Message for the owner":
        return Notice("customers", "important", summary, reason, "chat")
    segment: SegmentId = "money" if entry.channel == "scheduler" else "security"
    return Notice(segment, "important", summary, reason, "audit")


def _execution(entry: AuditEntry) -> Notice | None:
    action = entry.action or {}
    decision, summary, reason = entry.decision, entry.summary, entry.reason
    if decision == "BLOCKED":
        return None  # the scheduler writes a matching alert, which is the one shown
    if "payment_id" in action:
        if decision == "EXECUTED":
            return Notice("money", "info", summary, reason, "audit")
        if decision == "SCHEDULED":
            return Notice("money", "info", f"Scheduled: {summary}", reason, "audit")
        if decision == "FAILED":
            return Notice("money", "important", f"Failed: {summary}", reason, "audit")
        return None
    if "application_id" in action:
        if decision == "SUBMITTED":
            return Notice("cash", "info", f"Loan application sent: {summary}", reason, "loans")
        if decision == "FAILED":
            return Notice("cash", "important", f"Loan application failed: {summary}", reason, "loans")
        return None
    if summary.startswith("Baki reminder to"):
        if decision == "SENT":
            return Notice("customers", "info", summary, reason, "ledger")
        if decision == "FAILED":
            return Notice("customers", "important", f"Not delivered: {summary}", reason, "ledger")
        return None
    if decision == "CHANGED":
        return Notice("security", "info", f"Setting changed: {summary}", reason, "controls")
    return None


def classify(entry: AuditEntry) -> Notice | None:
    """The notice for one audit entry, or None when it is routine and stays in the audit log only."""
    event, decision = entry.event, entry.decision
    if event == "alert":
        return _alert(entry)
    if event == "notification":
        if entry.summary.startswith(("No more reminders to", "Reminders back on for")):
            return Notice("customers", "info", entry.summary, entry.reason, "ledger")
        segment: SegmentId = "money" if entry.channel == "scheduler" else "security"
        return Notice(segment, "info", entry.summary, entry.reason, "audit")
    if event == "kill_switch":
        if decision == "FROZEN":
            return Notice("security", "urgent", "STOP is on: everything is frozen", entry.reason, "controls")
        return Notice("security", "info", "STOP lifted: payments resumed", entry.reason, "controls")
    if event == "pin":
        if decision == "PIN_LOCKED":
            return Notice("security", "urgent", "PIN locked after wrong tries", entry.reason, "audit")
        if decision == "PIN_WRONG":
            return Notice("security", "important", "Wrong PIN entered", f"{entry.summary}. {entry.reason}", "audit")
        return None
    if event == "owner_control":
        return Notice("security", "info", f"Setting changed: {entry.summary}", entry.reason, "controls")
    if event == "report":
        return Notice("cash", "info", entry.summary, entry.reason, "audit")
    if event == "consent":
        return Notice("cash", "info", entry.summary, entry.reason, "credit")
    if event == "execution":
        return _execution(entry)
    return None


def _day(entry: AuditEntry) -> str:
    return datetime.fromisoformat(entry.ts).astimezone(BDT).strftime("%Y-%m-%d")


def _key(notice: Notice, day: str) -> str:
    digest = hashlib.sha256(f"{notice.segment}|{notice.title}|{day}".encode()).hexdigest()[:16]
    return f"ntf_{digest}"


def build(entries: list[AuditEntry], *, chain_id: str, per_segment: int = 40) -> dict:
    """Group `entries` (newest first) into the segments. Repeats of the same title on the same local
    day become one card with a count; each segment lists its newest cards first."""
    groups: dict[str, Group] = {}
    order: list[str] = []
    for entry in entries:
        notice = classify(entry)
        if notice is None:
            continue
        key = _key(notice, _day(entry))
        if key not in groups:
            groups[key] = Group(key=key)
            order.append(key)
        groups[key].entries.append(entry)
        groups[key].notices.append(notice)

    views = [groups[k].view() for k in order]
    segments = []
    for meta in SEGMENTS:
        items = [v for v in views if v["segment"] == meta["id"]][:per_segment]
        counts = {p: sum(1 for v in items if v["priority"] == p) for p in RANK}
        segments.append({**meta, "items": items, "counts": counts})
    return {
        "chain_id": chain_id,
        "latest_seq": entries[0].seq if entries else 0,
        "segments": segments,
    }
