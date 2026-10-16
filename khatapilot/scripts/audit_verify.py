"""Verify the hash chain of the audit log and print where it breaks, if it does.

    python scripts/audit_verify.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]

from app.core.clock import SystemClock  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.infra.audit_log import AuditLog  # noqa: E402


def main() -> int:
    audit = AuditLog(get_settings().audit_db_url, SystemClock())
    report = audit.verify()
    audit.dispose()
    if report.ok:
        print(f"Audit chain VERIFIED: {report.entries} entries, head {report.head_hash}")
        return 0
    print(f"Audit chain BROKEN at entry {report.first_bad_seq}: {report.problem}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
