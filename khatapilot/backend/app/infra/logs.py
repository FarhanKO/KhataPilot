"""Structured logs and error tracking for one backend process.

- Every log record is one JSON object per line (`LOG_FORMAT=json`, the default; `text` for reading in a
  terminal), on stderr and in `var/logs/khatapilot.jsonl` (rotated at 5 MB, five kept), with the time, level,
  logger, message and, for errors, the exception and its stack.
- Errors are also grouped: each ERROR record is fingerprinted by its exception type and the place it was
  raised, counted, and the first and last time and one example are kept in `var/logs/errors.json`. The
  count shows in `/api/metrics` and the groups in `/api/ops/errors` (same access rule as metrics), so a
  crash that repeats is one line to look at, not a thousand. With `SENTRY_DSN` set and `sentry-sdk`
  installed, errors also go to Sentry.

Nothing here logs a PIN, a token or a full phone number: request lines carry the method, path, status and
time only, and every message about the shop's data is already masked where it is written.
"""

from __future__ import annotations

import hashlib
import json
import logging
import logging.handlers
import sys
import threading
import traceback
from datetime import UTC, datetime
from pathlib import Path

from app.core.masking import mask_text

_configured = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        try:  # the request log line is already JSON: keep its fields as fields
            fields = json.loads(message) if message.startswith("{") else None
        except ValueError:
            fields = None
        out = {"ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
               "level": record.levelname.lower(), "logger": record.name}
        out.update(fields if isinstance(fields, dict) else {"msg": mask_text(message)})
        if record.exc_info:
            out["error"] = record.exc_info[0].__name__ if record.exc_info[0] else "Error"
            out["stack"] = mask_text(self.formatException(record.exc_info))[-4000:]
        return json.dumps(out, ensure_ascii=False, default=str)


class ErrorTracker(logging.Handler):
    """Groups ERROR records by fingerprint and keeps counts on disk."""

    def __init__(self, path: Path, on_error=None) -> None:
        super().__init__(level=logging.ERROR)
        self.path = path
        self.on_error = on_error
        self._lock = threading.Lock()
        try:
            self.groups: dict[str, dict] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, ValueError):
            self.groups = {}

    @staticmethod
    def fingerprint(record: logging.LogRecord) -> tuple[str, str]:
        if record.exc_info and record.exc_info[1] is not None:
            frames = traceback.extract_tb(record.exc_info[2])
            where = next((f"{Path(f.filename).name}:{f.lineno}" for f in reversed(frames)
                          if "site-packages" not in f.filename), f"{record.module}:{record.lineno}")
            kind = record.exc_info[0].__name__
        else:
            where, kind = f"{record.module}:{record.lineno}", "Logged"
        return hashlib.sha256(f"{kind}|{where}".encode()).hexdigest()[:12], f"{kind} at {where}"

    def emit(self, record: logging.LogRecord) -> None:
        try:
            key, title = self.fingerprint(record)
            now = datetime.now(UTC).isoformat(timespec="seconds")
            with self._lock:
                group = self.groups.setdefault(key, {"title": title, "count": 0, "first": now})
                group["count"] += 1
                group["last"] = now
                group["example"] = mask_text(record.getMessage())[:500]
                if record.exc_info:
                    group["stack"] = mask_text("".join(traceback.format_exception(*record.exc_info)))[-3000:]
                if len(self.groups) > 200:  # keep the newest groups
                    for old in sorted(self.groups, key=lambda k: self.groups[k].get("last", ""))[:50]:
                        self.groups.pop(old, None)
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(self.groups, indent=1, ensure_ascii=False), encoding="utf-8")
            if self.on_error:
                self.on_error()
        except Exception:  # noqa: BLE001  (error tracking must never break the request)
            self.handleError(record)

    def summary(self) -> list[dict]:
        with self._lock:
            rows = [{"id": k, **{f: v for f, v in g.items() if f != "stack"}} for k, g in self.groups.items()]
        return sorted(rows, key=lambda g: g.get("last", ""), reverse=True)


TRACKER: ErrorTracker | None = None


def configure(*, log_dir: Path, fmt: str = "json", level: str = "INFO", sentry_dsn: str = "",
              on_error=None) -> ErrorTracker:
    """Once per process. Returns the error tracker (also kept in `TRACKER`)."""
    global _configured, TRACKER
    if _configured and TRACKER is not None:
        TRACKER.on_error = on_error or TRACKER.on_error
        return TRACKER
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = JsonFormatter() if fmt == "json" else logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    rotating = logging.handlers.RotatingFileHandler(log_dir / "khatapilot.jsonl", maxBytes=5_000_000, backupCount=5,
                                                    encoding="utf-8")
    rotating.setFormatter(JsonFormatter())
    TRACKER = ErrorTracker(log_dir / "errors.json", on_error=on_error)
    app_log = logging.getLogger("app")
    http_log = logging.getLogger("khatapilot")
    for logger in (app_log, http_log):
        logger.setLevel(level.upper())
        logger.propagate = False
        for handler in (console, rotating, TRACKER):
            logger.addHandler(handler)
    for name in ("uvicorn.error",):  # server crashes are errors too
        logging.getLogger(name).addHandler(TRACKER)
    if sentry_dsn:
        try:
            import sentry_sdk

            sentry_sdk.init(dsn=sentry_dsn, send_default_pii=False, traces_sample_rate=0.0)
        except ImportError:
            app_log.warning("SENTRY_DSN is set but sentry-sdk is not installed; errors stay local")
    _configured = True
    return TRACKER
