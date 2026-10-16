"""The audit log: append-only and hash-chained, in its own SQLite file.

Every entry stores the hash of the entry before it, and its own hash covers every field, so
changing, removing or reordering any entry breaks the chain at that point. `verify()` walks the
chain and names the first broken link. SQLite triggers additionally abort any UPDATE or DELETE.

Phone numbers are masked before anything is written; PINs never reach this module.

    python scripts/audit_verify.py
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from datetime import UTC
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import Integer, String, Text, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.core.actions import canonical_json, sha256_hex
from app.core.clock import BDT, Clock
from app.core.ids import new_id
from app.core.masking import mask_data, mask_text
from app.infra.db import make_engine

log = logging.getLogger(__name__)

GENESIS_HASH = "0" * 64
HASHED_FIELDS = (
    "entry_id", "ts", "request_id", "channel", "actor", "provenance", "event", "summary",
    "action_json", "tier", "checks_json", "decision", "reason", "sandbox_ref", "decision_id", "prev_hash",
)
APPEND_ONLY_TRIGGERS = (
    "CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log "
    "BEGIN SELECT RAISE(ABORT, 'the audit log is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log "
    "BEGIN SELECT RAISE(ABORT, 'the audit log is append-only'); END",
)


class AuditBase(DeclarativeBase):
    pass


class AuditRow(AuditBase):
    __tablename__ = "audit_log"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry_id: Mapped[str] = mapped_column(String(40), unique=True)
    ts: Mapped[str] = mapped_column(String(40))  # ISO 8601 UTC, exactly as hashed
    request_id: Mapped[str] = mapped_column(String(64))
    channel: Mapped[str] = mapped_column(String(24))
    actor: Mapped[str] = mapped_column(String(64))
    provenance: Mapped[str] = mapped_column(String(16))
    event: Mapped[str] = mapped_column(String(32))
    summary: Mapped[str] = mapped_column(Text)
    action_json: Mapped[str] = mapped_column(Text)
    tier: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checks_json: Mapped[str] = mapped_column(Text)
    decision: Mapped[str] = mapped_column(String(24))
    reason: Mapped[str] = mapped_column(Text)
    sandbox_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decision_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class AuditRecord(BaseModel):
    """What callers hand to `append`."""

    request_id: str
    channel: str
    actor: str  # owner | external:<masked number> | agent | system | scheduler
    provenance: str  # owner | untrusted | system
    event: str  # decision | execution | approval | pin | owner_control | kill_switch | injection | system
    summary: str
    action: dict[str, Any] | None = None
    tier: int | None = None
    checks: list[dict[str, Any]] = Field(default_factory=list)
    decision: str  # ALLOWED | HELD | BLOCKED | EXECUTED | FAILED | REJECTED | INFO
    reason: str
    sandbox_ref: str | None = None
    decision_id: str | None = None


class AuditEntry(BaseModel):
    seq: int
    entry_id: str
    ts: str
    request_id: str
    channel: str
    actor: str
    provenance: str
    event: str
    summary: str
    action: dict[str, Any] | None
    tier: int | None
    checks: list[dict[str, Any]]
    decision: str
    reason: str
    sandbox_ref: str | None
    decision_id: str | None
    prev_hash: str
    hash: str

    @property
    def local_time(self) -> str:
        from datetime import datetime

        return datetime.fromisoformat(self.ts).astimezone(BDT).strftime("%d %b %Y %H:%M:%S")


class ChainReport(BaseModel):
    ok: bool
    entries: int
    head_hash: str
    first_bad_seq: int | None = None
    problem: str | None = None


def entry_hash(fields: dict[str, Any]) -> str:
    return sha256_hex(canonical_json({name: fields[name] for name in HASHED_FIELDS}))


def _view(row: AuditRow) -> AuditEntry:
    return AuditEntry(
        seq=row.seq, entry_id=row.entry_id, ts=row.ts, request_id=row.request_id, channel=row.channel,
        actor=row.actor, provenance=row.provenance, event=row.event, summary=row.summary,
        action=json.loads(row.action_json), tier=row.tier, checks=json.loads(row.checks_json),
        decision=row.decision, reason=row.reason, sandbox_ref=row.sandbox_ref, decision_id=row.decision_id,
        prev_hash=row.prev_hash, hash=row.hash,
    )


class AuditLog:
    def __init__(self, url: str, clock: Clock) -> None:
        self.clock = clock
        self.engine = make_engine(url)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        self._lock = threading.Lock()
        self._listeners: list[Callable[[AuditEntry], None]] = []
        self._create()

    def _create(self) -> None:
        AuditBase.metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            for statement in APPEND_ONLY_TRIGGERS:
                connection.execute(text(statement))

    def subscribe(self, listener: Callable[[AuditEntry], None]) -> None:
        self._listeners.append(listener)

    def append(self, record: AuditRecord) -> AuditEntry:
        fields: dict[str, Any] = {
            "entry_id": new_id("aud"),
            "ts": self.clock.now().astimezone(UTC).isoformat(timespec="microseconds"),
            "request_id": record.request_id,
            "channel": record.channel,
            "actor": mask_text(record.actor),
            "provenance": record.provenance,
            "event": record.event,
            "summary": mask_text(record.summary),
            "action_json": canonical_json(mask_data(record.action)).decode("utf-8"),
            "tier": record.tier,
            "checks_json": canonical_json(mask_data(record.checks)).decode("utf-8"),
            "decision": record.decision,
            "reason": mask_text(record.reason),
            "sandbox_ref": record.sandbox_ref,
            "decision_id": record.decision_id,
        }
        with self._lock, self.sessions.begin() as session:
            last = session.scalars(select(AuditRow).order_by(AuditRow.seq.desc()).limit(1)).first()
            fields["prev_hash"] = last.hash if last else GENESIS_HASH
            fields["hash"] = entry_hash(fields)
            row = AuditRow(**fields)
            session.add(row)
            session.flush()
            entry = _view(row)
        for listener in self._listeners:
            try:
                listener(entry)
            except Exception:  # a broken UI stream must never break auditing
                log.exception("audit listener failed")
        return entry

    def verify(self) -> ChainReport:
        previous, count = GENESIS_HASH, 0
        with self.sessions() as session:
            for row in session.scalars(select(AuditRow).order_by(AuditRow.seq)).yield_per(500):
                if row.prev_hash != previous:
                    return ChainReport(ok=False, entries=count, head_hash=previous, first_bad_seq=row.seq,
                                       problem="The link to the previous entry is broken: an entry was removed, "
                                               "inserted or reordered.")
                fields = {name: getattr(row, name) for name in HASHED_FIELDS}
                if entry_hash(fields) != row.hash:
                    return ChainReport(ok=False, entries=count, head_hash=previous, first_bad_seq=row.seq,
                                       problem="The entry's content does not match its hash: it was modified.")
                previous, count = row.hash, count + 1
        return ChainReport(ok=True, entries=count, head_hash=previous)

    def entries(self, *, since_seq: int = 0, limit: int = 200, event: str | None = None,
                decision: str | None = None, newest_first: bool = False) -> list[AuditEntry]:
        query = select(AuditRow).where(AuditRow.seq > since_seq)
        if event:
            query = query.where(AuditRow.event == event)
        if decision:
            query = query.where(AuditRow.decision == decision)
        query = query.order_by(AuditRow.seq.desc() if newest_first else AuditRow.seq).limit(limit)
        with self.sessions() as session:
            return [_view(r) for r in session.scalars(query)]

    def head(self) -> str:
        with self.sessions() as session:
            last = session.scalars(select(AuditRow).order_by(AuditRow.seq.desc()).limit(1)).first()
            return last.hash if last else GENESIS_HASH

    def reset(self) -> None:
        """Start a fresh chain. Used only by the seed and demo scripts on a local demo machine."""
        with self._lock:
            AuditBase.metadata.drop_all(self.engine)
            self._create()

    def dispose(self) -> None:
        self.engine.dispose()
