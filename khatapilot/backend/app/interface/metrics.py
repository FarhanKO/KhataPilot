"""`/api/metrics`: what a monitoring system (Prometheus, Grafana Agent, Uptime Kuma, ...) needs to watch one
shop's backend, in the Prometheus text format. Counts and states only: no names, numbers or amounts
of anyone's money.

Counted since the process started (counters):
- HTTP requests by method, route (ids replaced by `:id`) and status, and their latency by rate-limit bucket;
- requests refused by the rate limits;
- audit entries by event and decision (guard decisions, executions, alerts, PIN tries ...);
- background job runs by job and result, and when each job last succeeded.

Read when scraped (gauges): approvals waiting, STOP on or off, whether this process runs the scheduler,
payments by status, this month's AI spend against its budget and turns by planner, the audit chain's
length and whether it verifies (re-checked at most every five minutes), and the newest backup's age.

With `METRICS_TOKEN` set a scraper sends it as `Authorization: Bearer ...`; without one, only this
machine can read the metrics.
"""

from __future__ import annotations

import hmac
import re
import threading
import time
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from app import __version__
from app.guard.service import Guard
from app.infra import backup
from app.infra.audit_log import AuditEntry
from app.infra.job_state import JobLease
from app.interface.jobs import HOLDER

LATENCY_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)
MAX_ROUTES = 200
CHAIN_CHECK_SECONDS = 300
LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}
ID_SEGMENT = re.compile(r"\d|^[a-z]{2,8}_[A-Za-z0-9]{6,}$|^.{33,}$")


def route_label(path: str) -> str:
    """`/api/approvals/apr_7Hk2.../approve` -> `/api/approvals/:id/approve`, so labels stay few."""
    if not path.startswith("/api/"):
        return "(not api)"
    return "/api/" + "/".join(":id" if ID_SEGMENT.search(s) else s for s in path[5:].split("/"))


def _escape(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _labels(names: tuple[str, ...], values: tuple) -> str:
    if not names:
        return ""
    return "{" + ",".join(f'{n}="{_escape(v)}"' for n, v in zip(names, values, strict=True)) + "}"


class Metrics:
    """In-process counters and a latency histogram. Thread-safe; one per backend process."""

    def __init__(self) -> None:
        self.started = time.time()
        self._lock = threading.Lock()
        self.requests: dict[tuple[str, str, str], int] = defaultdict(int)
        self.latency: dict[str, list[int]] = defaultdict(lambda: [0] * (len(LATENCY_BUCKETS) + 1))
        self.latency_sum: dict[str, float] = defaultdict(float)
        self.limited: dict[str, int] = defaultdict(int)
        self.audit: dict[tuple[str, str], int] = defaultdict(int)
        self.jobs: dict[tuple[str, str], int] = defaultdict(int)
        self.job_success: dict[str, float] = {}
        self._chain: tuple[float, bool, int] | None = None  # (checked at, ok, entries)
        self.voice = [0, 0.0]  # recordings transcribed here, seconds spent
        self.errors = 0  # unhandled errors (500s)

    # ---- recording

    def observe_request(self, method: str, path: str, status: int, seconds: float, bucket: str | None,
                        limited: bool = False) -> None:
        route = route_label(path)
        with self._lock:
            if (method, route) not in {(m, r) for m, r, _ in self.requests} and \
                    len({r for _, r, _ in self.requests}) >= MAX_ROUTES:
                route = "(other)"
            self.requests[(method, route, str(status))] += 1
            name = bucket or "none"
            index = next((i for i, le in enumerate(LATENCY_BUCKETS) if seconds <= le), len(LATENCY_BUCKETS))
            self.latency[name][index] += 1
            self.latency_sum[name] += seconds
            if limited:
                self.limited[name] += 1

    def observe_audit(self, entry: AuditEntry) -> None:
        with self._lock:
            self.audit[(entry.event, entry.decision)] += 1

    def voice_transcribed(self, seconds: float) -> None:
        with self._lock:
            self.voice[0] += 1
            self.voice[1] += seconds

    def error(self) -> None:
        with self._lock:
            self.errors += 1

    def job_finished(self, job: str, ok: bool) -> None:
        with self._lock:
            self.jobs[(job, "ok" if ok else "failed")] += 1
            if ok:
                self.job_success[job] = time.time()

    # ---- the exposition

    def render(self, container) -> str:
        out: list[str] = []

        def metric(name: str, kind: str, help_text: str, rows: Iterable[tuple[tuple[str, ...], tuple, float]]) -> None:
            out.append(f"# HELP {name} {help_text}")
            out.append(f"# TYPE {name} {kind}")
            for names, values, value in rows:
                out.append(f"{name}{_labels(names, values)} {value:g}" if isinstance(value, float)
                           else f"{name}{_labels(names, values)} {value}")

        settings = container.settings
        with self._lock:
            requests = dict(self.requests)
            latency = {k: list(v) for k, v in self.latency.items()}
            latency_sum = dict(self.latency_sum)
            limited, audit, jobs, success = dict(self.limited), dict(self.audit), dict(self.jobs), dict(self.job_success)

        metric("khatapilot_info", "gauge", "Version and configuration of this backend.",
               [(("version", "llm_mode", "model", "risk_model", "shop"),
                 (__version__, settings.llm_mode, container.provider.model, container.risk_model_name,
                  settings.shop_id), 1)])
        metric("khatapilot_process_start_time_seconds", "gauge", "When this process started (unix time).",
               [((), (), float(round(self.started, 3)))])
        metric("khatapilot_errors_total", "counter", "Unhandled errors (answered with a plain 500).",
               [((), (), self.errors)])
        metric("khatapilot_voice_transcriptions_total", "counter", "Recordings turned into text on this machine.",
               [((), (), self.voice[0])])
        metric("khatapilot_voice_seconds_total", "counter", "Seconds spent turning recordings into text.",
               [((), (), float(round(self.voice[1], 3)))])
        metric("khatapilot_http_requests_total", "counter", "HTTP requests by method, route and status.",
               [(("method", "route", "status"), k, v) for k, v in sorted(requests.items())])
        out.append("# HELP khatapilot_http_request_duration_seconds Request latency by rate-limit bucket.")
        out.append("# TYPE khatapilot_http_request_duration_seconds histogram")
        for name in sorted(latency):
            running = 0
            for le, count in zip((*LATENCY_BUCKETS, "+Inf"), latency[name], strict=True):
                running += count
                out.append(f'khatapilot_http_request_duration_seconds_bucket{{bucket="{name}",le="{le}"}} {running}')
            out.append(f'khatapilot_http_request_duration_seconds_sum{{bucket="{name}"}} {latency_sum[name]:.6f}')
            out.append(f'khatapilot_http_request_duration_seconds_count{{bucket="{name}"}} {running}')
        metric("khatapilot_http_rate_limited_total", "counter", "Requests refused by the per-minute limits.",
               [(("bucket",), (k,), v) for k, v in sorted(limited.items())])
        metric("khatapilot_audit_entries_total", "counter", "Audit entries written since start, by event and decision.",
               [(("event", "decision"), k, v) for k, v in sorted(audit.items())])
        metric("khatapilot_job_runs_total", "counter", "Background job runs by job and result.",
               [(("job", "result"), k, v) for k, v in sorted(jobs.items())])
        metric("khatapilot_job_last_success_timestamp_seconds", "gauge", "When each job last succeeded (unix time).",
               [(("job",), (k,), float(round(v, 3))) for k, v in sorted(success.items())])

        with container.db.session() as session:
            guard = Guard(session, container.guard_deps)
            pending = len(guard.approvals.pending())
            frozen = guard.store.kill_switch().frozen
            payments = guard.payments.status_counts()
            lease = session.get(JobLease, "scheduler")

        metric("khatapilot_approvals_pending", "gauge", "Held actions waiting for the owner.", [((), (), pending)])
        metric("khatapilot_kill_switch_on", "gauge", "1 while STOP is on.", [((), (), int(frozen))])
        leader = lease is not None and lease.holder == HOLDER and lease.expires_at.timestamp() > time.time()
        metric("khatapilot_scheduler_leader", "gauge", "1 if this process holds the scheduler lease.",
               [((), (), int(leader))])
        metric("khatapilot_payments", "gauge", "The shop's payments by status.",
               [(("status",), (k,), v) for k, v in sorted(payments.items())])

        if container.budget is not None:
            month = container.budget.status()
            metric("khatapilot_llm_month_spend_usd", "gauge", "AI spend this calendar month (list prices).",
                   [((), (), float(month["spent_usd"]))])
            metric("khatapilot_llm_month_budget_usd", "gauge", "The monthly AI budget (0: none).",
                   [((), (), float(month["budget_usd"] or 0))])
            metric("khatapilot_llm_month_capped", "gauge", "1 while the month's budget is used up.",
                   [((), (), int(month["capped"]))])
            metric("khatapilot_llm_month_calls", "gauge", "Calls to the model this month.", [((), (), month["calls"])])
            metric("khatapilot_llm_month_turns", "gauge", "Chat turns this month by who planned them.",
                   [(("route",), (k,), v) for k, v in sorted(month["turns"].items())])

        ok, entries = self._chain_state(container)
        metric("khatapilot_audit_chain_ok", "gauge", "1 if the audit log's hash chain verifies.", [((), (), int(ok))])
        metric("khatapilot_audit_chain_entries", "gauge", "Entries in the audit log.", [((), (), entries)])

        newest = backup.backups(settings.backup_dir)
        regular = [b for b in newest if not b["folder"].name.startswith(backup.PRE_RESTORE)]
        metric("khatapilot_backups_kept", "gauge", "Backups in the backup folder.", [((), (), len(regular))])
        if regular:
            made = datetime.fromisoformat(regular[0]["created_at"]).timestamp()
            metric("khatapilot_backup_last_timestamp_seconds", "gauge", "When the newest backup was made.",
                   [((), (), float(made))])
        return "\n".join(out) + "\n"

    def _chain_state(self, container) -> tuple[bool, int]:
        now = time.time()
        if self._chain is None or now - self._chain[0] > CHAIN_CHECK_SECONDS:
            report = container.audit.verify()
            self._chain = (now, report.ok, report.entries)
        return self._chain[1], self._chain[2]


router = APIRouter(tags=["metrics"])


def _ops_access(request: Request) -> None:
    """Metrics and error groups: a bearer METRICS_TOKEN, or (without one) this machine only."""
    token = request.app.state.container.settings.metrics_token.get_secret_value()
    if token:
        given = request.headers.get("authorization", "")
        if not hmac.compare_digest(given.encode(), f"Bearer {token}".encode()):
            raise HTTPException(401, "Send the metrics token as a bearer token.")
    elif (request.client.host if request.client else "") not in LOOPBACK:
        raise HTTPException(403, "Metrics are only served to this machine unless METRICS_TOKEN is set.")


@router.get("/ops/errors")
def errors(request: Request) -> dict:
    """Errors grouped by where they happen (data_dir/logs/errors.json), newest first."""
    _ops_access(request)
    from app.infra import logs

    groups = logs.TRACKER.summary() if logs.TRACKER else []
    return {"groups": groups, "total": sum(g.get("count", 0) for g in groups), "tracking": logs.TRACKER is not None}


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(request: Request) -> PlainTextResponse:
    container = request.app.state.container
    _ops_access(request)
    if container.metrics is None:
        raise HTTPException(503, "Metrics are not collected in this process.")
    return PlainTextResponse(container.metrics.render(container),
                             media_type="text/plain; version=0.0.4; charset=utf-8")
