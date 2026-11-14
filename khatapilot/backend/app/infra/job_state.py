"""Background-job bookkeeping in the shop database: who runs the scheduler, and which once-a-period
checks already ran.

- A lease (`JobLease`): only the process holding the "scheduler" lease runs due payments and the daily
  checks. Start two backend workers on one database and the second waits instead of paying twice.
  The lease uses wall-clock time (processes die in real time, whatever the simulated clock says) and
  lapses if its holder stops renewing it.
- A mark (`JobMark`): "the cash warning for 20 Oct was sent today", "October's savings alert went out".
  Inserting the key is atomic, so a check that runs twice, in one process or two, alerts once. Before
  this, the checks searched the last few audit entries, which a busy day could push out of reach.

Both tables are reset with the demo shop (they live on the same `Base`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import String, delete, or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.infra.db import Base


class JobLease(Base):
    __tablename__ = "job_leases"

    name: Mapped[str] = mapped_column(String(48), primary_key=True)
    holder: Mapped[str] = mapped_column(String(96))
    expires_at: Mapped[datetime]


class JobMark(Base):
    __tablename__ = "job_marks"

    key: Mapped[str] = mapped_column(String(96), primary_key=True)
    created_at: Mapped[datetime]


def _now() -> datetime:
    return datetime.now(UTC)


def acquire_lease(session: Session, name: str, holder: str, ttl: timedelta) -> bool:
    """Take or renew the lease. True if `holder` now holds it until now + ttl."""
    now = _now()
    renewed = session.execute(
        update(JobLease).where(JobLease.name == name, or_(JobLease.holder == holder, JobLease.expires_at < now))
        .values(holder=holder, expires_at=now + ttl))
    if renewed.rowcount:
        session.flush()
        return True
    try:
        with session.begin_nested():
            session.add(JobLease(name=name, holder=holder, expires_at=now + ttl))
        return True
    except IntegrityError:
        return False  # someone else holds a live lease


def release_lease(session: Session, name: str, holder: str) -> None:
    session.execute(delete(JobLease).where(JobLease.name == name, JobLease.holder == holder))


def mark_once(session: Session, key: str) -> bool:
    """Record `key`. True the first time; False if it was already recorded (by anyone)."""
    try:
        with session.begin_nested():
            session.add(JobMark(key=key, created_at=_now()))
        return True
    except IntegrityError:
        return False


def is_marked(session: Session, key: str) -> bool:
    return session.get(JobMark, key) is not None
