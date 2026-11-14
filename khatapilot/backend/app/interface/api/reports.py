"""Printable monthly reports, opened from Alerts: a full statement for the owner, or a six-month statement
to hand to a bank or NGO with a loan application.

The report module works out the figures from the books; this route adds what only the guard knows (for
the owner: blocked attempts and alerts that month; for both: whether the books match the provider's
statements, whether the audit log's chain verifies, and a fingerprint of the report's figures), and
records in the audit log that a report was made. Nothing is sent anywhere: the owner prints it or saves
it as a PDF and decides who gets it.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.clock import BDT
from app.core.ids import new_id
from app.core.provenance import Sender
from app.guard.service import Guard
from app.interface.deps import get_container, require_owner
from app.modules.reports.schemas import NoSuchMonth, ReportMonth

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/months")
def months(container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> dict:
    with container.db.session() as session:
        try:
            found: list[ReportMonth] = Guard(session, container.guard_deps).reports.months()
        except NoSuchMonth as exc:
            raise HTTPException(409, str(exc)) from exc
    finished = [m for m in found if m.finished]
    default = (finished or found)[0].month if found else None  # the last finished month
    return {"months": [m.model_dump() for m in found], "default": default}


def _security(container, month: date) -> dict:
    """That month's guard activity, from the audit log."""
    counts = {"blocked": 0, "held_for_you": 0, "alerts": 0, "wrong_pin": 0, "stop_used": 0}
    for e in container.audit.entries(limit=20_000, newest_first=True):
        day = datetime.fromisoformat(e.ts).astimezone(BDT).date()
        if (day.year, day.month) != (month.year, month.month):
            continue
        if e.event == "decision" and e.decision == "BLOCKED":
            counts["blocked"] += 1
        elif e.event == "decision" and e.decision.startswith("HELD"):
            counts["held_for_you"] += 1
        elif e.event == "alert":
            counts["alerts"] += 1
        elif e.event == "pin" and e.decision in ("PIN_WRONG", "PIN_LOCKED"):
            counts["wrong_pin"] += 1
        elif e.event == "kill_switch" and e.decision == "FROZEN":
            counts["stop_used"] += 1
    return counts


def _fingerprint(body: dict) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


@router.get("/monthly")
def monthly(kind: Literal["owner", "lender"] = "owner",
            month: str = Query(pattern=r"^20\d\d-(0[1-9]|1[0-2])$", description="YYYY-MM"),
            container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> dict:
    when = date.fromisoformat(f"{month}-01")
    with container.db.session() as session:
        guard = Guard(session, container.guard_deps)
        try:
            report = guard.reports.owner(when) if kind == "owner" else guard.reports.lender(when)
        except NoSuchMonth as exc:
            raise HTTPException(404, str(exc)) from exc
        match = guard.payments.match_provider()
        body = report.model_dump(mode="json")
        if kind == "owner":
            body["security"] = _security(container, when)
        chain = container.audit.verify()
        body["integrity"] = {
            "provider": {"checked": container.clock.now().isoformat(), "reachable": match.reachable,
                         "matches": match.reachable and not match.problems, "problems": match.problems},
            "audit": {"entries": chain.entries, "verified": chain.ok, "head": chain.head_hash[:16]},
        }
        # the figures only (not the report's id or time), so the same books give the same fingerprint
        body["integrity"]["fingerprint"] = _fingerprint({k: v for k, v in body.items()
                                                         if k not in ("integrity", "id", "generated_at")})
        who = "for a bank or NGO" if kind == "lender" else "for you"
        guard.recorder.event(
            request_id=new_id("req"), channel="web", actor="owner", provenance="owner", event="report",
            summary=f"Monthly statement made {who}: {report.period}", decision="INFO",
            reason=(f"Report {report.id}, fingerprint {body['integrity']['fingerprint'][:16]}. Nothing was sent: the "
                    "owner prints it or saves it as a PDF."))
    return body
