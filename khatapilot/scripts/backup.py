"""Back up the books and the audit log, check backups, rehearse a restore, or restore one.

    python scripts/backup.py                  # make a backup now (var/backups/<time>/), checked, newest 14 kept
    python scripts/backup.py list             # the backups there are
    python scripts/backup.py verify [NAME]    # re-check one (default: the newest)
    python scripts/backup.py drill [NAME]     # restore it into a scratch folder and prove it is usable
    python scripts/backup.py restore NAME --yes   # put it back (stop the backend first)

A restore first saves what is there now as a `pre-restore-…` backup, so it can be undone the same way.
The folder, how many to keep and a daily backup by the backend itself are set in .env (BACKUP_DIR,
BACKUP_KEEP, BACKUP_DAILY). Only SQLite is handled; a Postgres database is backed up with pg_dump.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]

from app.bootstrap import register_models  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.infra import backup  # noqa: E402
from app.infra.db import Base  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", nargs="?", default="create", choices=["create", "list", "verify", "drill", "restore"])
    parser.add_argument("name", nargs="?", help="a backup's folder name (default: the newest)")
    parser.add_argument("--yes", action="store_true", help="really restore (it replaces the live databases)")
    parser.add_argument("--note", default="", help="a note kept in the manifest")
    args = parser.parse_args(argv)

    settings = get_settings()
    root = settings.backup_dir
    register_models()
    try:
        if args.command == "create":
            report = backup.create(app_url=settings.db_url, audit_url=settings.audit_db_url, root=root,
                                   keep=settings.backup_keep, note=args.note)
            print(f"Backup {report.name} made in {report.folder}")
        elif args.command == "list":
            found = backup.backups(root)
            if not found:
                print(f"No backups in {root}.")
            for b in found:
                rows = sum(sum(f["tables"].values()) for f in b["files"].values())
                print(f"{b['folder'].name:28} {b['created_at']}  {rows:>7} rows  audit {b['audit']['entries']} entries"
                      + (f"  ({b['note']})" if b.get("note") else ""))
            return 0
        elif args.command == "verify":
            report = backup.verify(root, args.name, metadata=Base.metadata)
        elif args.command == "drill":
            report = backup.drill(root, args.name, metadata=Base.metadata)
        else:
            if not args.name or not args.yes:
                print("A restore replaces the live databases. Stop the backend, then run:\n"
                      "    python scripts/backup.py restore <NAME> --yes\n"
                      "(what is there now is saved first as a pre-restore backup).")
                return 2
            report = backup.restore(app_url=settings.db_url, audit_url=settings.audit_db_url, root=root,
                                    name=args.name, metadata=Base.metadata)
    except backup.BackupError as problem:
        print(f"FAILED: {problem}")
        return 1
    print(backup.lines([report]))
    print(("PASSED" if report.ok else "FAILED") + f": {args.command} {report.name}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
