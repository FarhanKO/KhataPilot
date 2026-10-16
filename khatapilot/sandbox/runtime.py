"""Simulator state shared by the routes: the simulated clock, id counters and the operation log."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from sandbox.models import OpLogEntry, SimState

BDT = timezone(timedelta(hours=6), name="BDT")
MSISDN_RE = re.compile(r"01[3-9]\d{8}")
TRX_PREFIX = {"bkash": "B", "nagad": "N", "bank": "T"}


def mask_number(number: str | None) -> str | None:
    """01712345678 -> 017XXXX5678; bank account numbers -> ****1234."""
    if not number:
        return number
    if MSISDN_RE.fullmatch(number):
        return f"{number[:3]}XXXX{number[-4:]}"
    return f"****{number[-4:]}" if len(number) > 4 else "****"


def is_msisdn(number: str) -> bool:
    return bool(MSISDN_RE.fullmatch(number))


def next_seq(session: Session, name: str) -> int:
    row = session.get(SimState, f"seq:{name}")
    if row is None:
        row = SimState(key=f"seq:{name}", value="0")
        session.add(row)
    value = int(row.value) + 1
    row.value = str(value)
    return value


def next_trx_id(session: Session, provider: str) -> str:
    """Deterministic, realistic-looking TrxIDs: the same sequence after every reset."""
    n = next_seq(session, f"trx:{provider}")
    return TRX_PREFIX.get(provider, "X") + hashlib.sha256(f"{provider}:{n}".encode()).hexdigest()[:9].upper()


class SandboxClock:
    """Simulated time, persisted so it survives restarts. Advances with wall time unless frozen."""

    KEY = "clock"

    def __init__(self, sessions: sessionmaker, default_start: datetime | None) -> None:
        self._sessions = sessions
        self._lock = threading.Lock()
        self._default_start = default_start
        with sessions() as session:
            row = session.get(SimState, self.KEY)
            state = json.loads(row.value) if row else None
        if state:
            self._sim = datetime.fromisoformat(state["sim"])
            self._wall = datetime.fromisoformat(state["wall"])
            self._frozen = bool(state["frozen"])
        else:
            self.reset()

    def now(self) -> datetime:
        with self._lock:
            if self._frozen:
                return self._sim
            return self._sim + (datetime.now(UTC) - self._wall)

    @property
    def frozen(self) -> bool:
        return self._frozen

    def set(self, moment: datetime, *, frozen: bool = False) -> None:
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=BDT)
        with self._lock:
            self._sim, self._wall, self._frozen = moment, datetime.now(UTC), frozen
            payload = json.dumps({"sim": self._sim.isoformat(), "wall": self._wall.isoformat(), "frozen": frozen})
        with self._sessions.begin() as session:
            row = session.get(SimState, self.KEY)
            if row is None:
                session.add(SimState(key=self.KEY, value=payload))
            else:
                row.value = payload

    def reset(self) -> None:
        self.set(self._default_start or datetime.now(UTC))


class OpLog:
    """Append-only operation log, in the sandbox DB and as JSON lines in `oplog.jsonl`.

    Each record is written in its own transaction, after the business transaction has finished,
    so rejected requests are logged even though their business changes were rolled back.
    """

    def __init__(self, sessions: sessionmaker, path: Path, clock: SandboxClock) -> None:
        self._sessions = sessions
        self._path = path
        self._clock = clock
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, *, operation: str, status: str, code: str, detail: str, **fields: Any) -> int:
        with self._lock:
            with self._sessions.begin() as session:
                entry = OpLogEntry(
                    at=self._clock.now(),
                    wall_at=datetime.now(UTC),
                    operation=operation,
                    status=status,
                    code=code,
                    detail=detail,
                    **fields,
                )
                session.add(entry)
                session.flush()
                line = to_json(entry)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(line, ensure_ascii=False) + "\n")
            return line["seq"]

    def clear_file(self) -> None:
        with self._lock:
            self._path.write_text("", encoding="utf-8")


def to_json(entry: OpLogEntry) -> dict[str, Any]:
    return {
        "seq": entry.seq,
        "at": entry.at.astimezone(BDT).isoformat(),
        "wall_at": entry.wall_at.isoformat(),
        "operation": entry.operation,
        "status": entry.status,
        "code": entry.code,
        "detail": entry.detail,
        "account_id": entry.account_id,
        "counterparty": entry.counterparty,
        "amount_paisa": entry.amount_paisa,
        "ref_id": entry.ref_id,
        "authorization_id": entry.authorization_id,
        "idempotency_key": entry.idempotency_key,
        "decision_id": entry.decision_id,
        "simulated": True,
    }
