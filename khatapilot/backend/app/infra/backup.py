"""Backups of the two SQLite files (the books and the audit log), checked when they are made and when
they are restored.

A backup is a folder under the backup directory holding a consistent copy of each database, taken with
SQLite's online backup API (safe while the backend runs), and a manifest: each file's size and SHA-256,
the rows in every table, and the audit chain's length and head hash. A copy is only kept if it passes
SQLite's integrity check and its audit chain verifies.

`drill()` restores a backup into a scratch folder and proves it is usable: checksums, integrity, rows
equal to the manifest, an intact audit chain, and every table the current code defines readable from
it. `restore()` first backs up what is there now (so a restore can itself be undone), then copies the
backup into place and runs the same checks on the result.

Only SQLite URLs are handled; a server database (Postgres) is backed up with its own tools.

    python scripts/backup.py            # make one now
    python scripts/backup.py drill      # restore the latest into a scratch folder and check it
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import MetaData, create_engine, func, select
from sqlalchemy.engine import make_url

from app.infra.audit_log import GENESIS_HASH, HASHED_FIELDS, entry_hash
from app.infra.db import Base, Database

MANIFEST = "manifest.json"
FILES = {"app": "khatapilot.db", "audit": "audit.db"}
PRE_RESTORE = "pre-restore-"


class BackupError(Exception):
    """A backup could not be made, or a copy failed a check. The message says which and why."""


def sqlite_path(url: str) -> Path:
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite" or not parsed.database or parsed.database == ":memory:":
        raise BackupError(f"Only SQLite files are backed up here ({parsed.get_backend_name()} is not); use the "
                          "database's own tools, for example pg_dump.")
    return Path(parsed.database)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _connect(path: Path, *, read_only: bool = True) -> sqlite3.Connection:
    if read_only:
        return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    return sqlite3.connect(path)


def copy_database(source: Path, target: Path) -> None:
    """A consistent copy of a live database, as one file (no -wal beside it)."""
    if not source.exists():
        raise BackupError(f"{source} does not exist.")
    target.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(source)  # sees committed rows still in the WAL; reads only
    dst = sqlite3.connect(target)
    try:
        src.execute("PRAGMA busy_timeout=5000")
        src.backup(dst)
        dst.execute("PRAGMA journal_mode=DELETE")
        dst.commit()
    finally:
        dst.close()
        src.close()


def integrity(path: Path) -> str:
    connection = _connect(path)  # closed explicitly: `with` on a sqlite3 connection only commits
    try:
        return connection.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        connection.close()


def row_counts(path: Path) -> dict[str, int]:
    connection = _connect(path)
    try:
        names = [r[0] for r in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {name: connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] for name in names}
    finally:
        connection.close()


@dataclass(frozen=True)
class Chain:
    ok: bool
    entries: int
    head_hash: str
    problem: str | None = None


def audit_chain(path: Path) -> Chain:
    """The audit log's hash chain, walked straight from the file (nothing is written to it)."""
    connection = _connect(path)
    connection.row_factory = sqlite3.Row
    previous, count = GENESIS_HASH, 0
    try:
        rows = connection.execute(f"SELECT seq, hash, {', '.join(HASHED_FIELDS)} FROM audit_log ORDER BY seq")
        for row in rows:
            if row["prev_hash"] != previous:
                return Chain(False, count, previous, f"entry {row['seq']} does not link to the one before it")
            if entry_hash({name: row[name] for name in HASHED_FIELDS}) != row["hash"]:
                return Chain(False, count, previous, f"entry {row['seq']} was changed after it was written")
            previous, count = row["hash"], count + 1
    except sqlite3.DatabaseError as exc:
        return Chain(False, count, previous, f"the audit log cannot be read ({exc})")
    finally:
        connection.close()
    return Chain(True, count, previous)


def readable_by_code(path: Path, metadata: MetaData) -> list[str]:
    """Tables the current code defines that it cannot read from this copy (empty: all readable).

    Works on a throwaway copy, because opening it the app's way may add columns."""
    problems = []
    with tempfile.TemporaryDirectory() as scratch:
        probe = Path(scratch) / path.name
        shutil.copy2(path, probe)
        engine = create_engine(f"sqlite:///{probe.as_posix()}")
        try:
            if metadata is Base.metadata:  # open it as the backend would, adding any new columns
                database = Database(f"sqlite:///{probe.as_posix()}")
                database.create_all()
                database.dispose()
            with engine.connect() as connection:
                for table in metadata.sorted_tables:
                    try:
                        connection.execute(select(func.count()).select_from(table)).scalar_one()
                    except Exception as exc:  # a missing table or column
                        problems.append(f"{table.name}: {type(exc).__name__}")
        finally:
            engine.dispose()
    return problems


@dataclass
class Report:
    """What a backup, a check, a drill or a restore found."""

    name: str
    folder: Path
    ok: bool = True
    lines: list[str] = field(default_factory=list)

    def say(self, line: str, *, bad: bool = False) -> None:
        self.lines.append(("FAIL " if bad else "ok   ") + line)
        if bad:
            self.ok = False


def _stamp(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y%m%d-%H%M%S")


def _unique(root: Path, name: str) -> Path:
    folder, n = root / name, 1
    while folder.exists():
        n += 1
        folder = root / f"{name}-{n}"
    return folder


def create(*, app_url: str, audit_url: str, root: Path, keep: int = 14, now: datetime | None = None,
           name: str | None = None, note: str = "") -> Report:
    """Copy both databases into a new folder under `root`, check the copies and write the manifest.
    A copy that fails a check is deleted and the error raised, so a bad backup is never kept."""
    now = now or datetime.now(UTC)
    folder = _unique(root, name or _stamp(now))
    sources = {"app": sqlite_path(app_url), "audit": sqlite_path(audit_url)}
    folder.mkdir(parents=True)
    report = Report(folder.name, folder)
    try:
        files = {}
        for role, source in sources.items():
            target = folder / FILES[role]
            copy_database(source, target)
            check = integrity(target)
            if check != "ok":
                raise BackupError(f"the copy of {source.name} failed SQLite's integrity check: {check}")
            files[role] = {"file": target.name, "source": str(source), "bytes": target.stat().st_size,
                           "sha256": sha256_file(target), "tables": row_counts(target)}
            report.say(f"{source.name}: {sum(files[role]['tables'].values())} rows in "
                       f"{len(files[role]['tables'])} tables, integrity ok")
        chain = audit_chain(folder / FILES["audit"])
        if not chain.ok:
            raise BackupError(f"the audit log in the backup does not verify: {chain.problem}")
        report.say(f"audit chain verified: {chain.entries} entries, head {chain.head_hash[:12]}")
        manifest = {"name": folder.name, "created_at": now.astimezone(UTC).isoformat(timespec="seconds"),
                    "note": note, "files": files,
                    "audit": {"entries": chain.entries, "head_hash": chain.head_hash}}
        (folder / MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    removed = prune(root, keep) if keep > 0 else []
    if removed:
        report.say(f"kept the newest {keep}; removed {', '.join(removed)}")
    return report


def backups(root: Path) -> list[dict]:
    """Every backup under `root` with a manifest, newest first."""
    found = []
    for manifest in root.glob(f"*/{MANIFEST}") if root.exists() else []:
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        found.append({**data, "folder": manifest.parent})
    return sorted(found, key=lambda d: (d.get("created_at", ""), d["folder"].name), reverse=True)


def prune(root: Path, keep: int) -> list[str]:
    """Remove the oldest regular backups beyond `keep`. Safety copies made before a restore are kept."""
    regular = [b for b in backups(root) if not b["folder"].name.startswith(PRE_RESTORE)]
    removed = []
    for old in regular[keep:]:
        shutil.rmtree(old["folder"], ignore_errors=True)
        removed.append(old["folder"].name)
    return removed


def find(root: Path, name: str | None) -> dict:
    found = backups(root)
    if not found:
        raise BackupError(f"There are no backups in {root}.")
    if name is None:
        return found[0]
    match = next((b for b in found if b["folder"].name == name), None)
    if match is None:
        raise BackupError(f"No backup named {name!r} in {root}.")
    return match


def check_copy(manifest: dict, folder: Path, report: Report, *, metadata: MetaData | None = None,
               checksums: bool = True) -> None:
    """The checks a usable copy passes, written to `report`."""
    for role, info in manifest["files"].items():
        path = folder / info["file"]
        if not path.exists():
            report.say(f"{info['file']} is missing", bad=True)
            continue
        if checksums:
            same = sha256_file(path) == info["sha256"]
            report.say(f"{info['file']} checksum {'matches' if same else 'DOES NOT match'} the manifest", bad=not same)
        check = integrity(path)
        report.say(f"{info['file']} integrity {check}", bad=check != "ok")
        counts = row_counts(path)
        differ = sorted(t for t in set(counts) | set(info["tables"]) if counts.get(t) != info["tables"].get(t))
        report.say(f"{info['file']} rows match the manifest ({sum(counts.values())} rows)" if not differ
                   else f"{info['file']} rows differ from the manifest in {', '.join(differ)}", bad=bool(differ))
        if role == "app" and metadata is not None:
            problems = readable_by_code(path, metadata)
            report.say(f"{info['file']}: every table the current code uses can be read" if not problems
                       else f"{info['file']}: the current code cannot read {', '.join(problems)}", bad=bool(problems))
    chain = audit_chain(folder / manifest["files"]["audit"]["file"])
    expected = manifest["audit"]
    same_head = chain.head_hash == expected["head_hash"] and chain.entries == expected["entries"]
    if not chain.ok:
        report.say(f"audit chain broken: {chain.problem}", bad=True)
    else:
        report.say(f"audit chain verified: {chain.entries} entries"
                   + ("" if same_head else " (but not the head the manifest recorded)"), bad=not same_head)


def verify(root: Path, name: str | None = None, *, metadata: MetaData | None = None) -> Report:
    manifest = find(root, name)
    folder = manifest["folder"]
    report = Report(folder.name, folder)
    check_copy(manifest, folder, report, metadata=metadata)
    return report


def drill(root: Path, name: str | None = None, *, metadata: MetaData | None = None,
          scratch: Path | None = None) -> Report:
    """Restore a backup into a scratch folder and check it there: a restore rehearsal that touches
    nothing the backend uses."""
    manifest = find(root, name)
    folder = manifest["folder"]
    report = Report(folder.name, folder)
    with tempfile.TemporaryDirectory(dir=scratch) as tmp:
        target = Path(tmp)
        for info in manifest["files"].values():
            restore_file(folder / info["file"], target / info["file"])
        report.say(f"restored {folder.name} into a scratch folder")
        check_copy(manifest, target, report, metadata=metadata, checksums=False)
    return report


def restore_file(source: Path, target: Path) -> None:
    """Copy a backup file over `target` page by page (SQLite's backup API), so a target in WAL mode is
    replaced whole and nothing is left half-written."""
    target.parent.mkdir(parents=True, exist_ok=True)
    src = _connect(source)
    dst = sqlite3.connect(target)
    try:
        dst.execute("PRAGMA busy_timeout=5000")
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()


def restore(*, app_url: str, audit_url: str, root: Path, name: str | None = None,
            metadata: MetaData | None = None, now: datetime | None = None) -> Report:
    """Put a backup back in place. What is there now is backed up first (a `pre-restore-` folder), so this
    can be undone with another restore. Stop the backend first: a request in flight would be lost."""
    manifest = find(root, name)
    folder = manifest["folder"]
    targets = {"app": sqlite_path(app_url), "audit": sqlite_path(audit_url)}
    report = Report(folder.name, folder)
    if all(path.exists() for path in targets.values()):
        safety = create(app_url=app_url, audit_url=audit_url, root=root, keep=0, now=now,
                        name=f"{PRE_RESTORE}{_stamp(now or datetime.now(UTC))}", note=f"before restoring {folder.name}")
        report.say(f"saved what was there first as {safety.name}")
    for role, info in manifest["files"].items():
        restore_file(folder / info["file"], targets[role])
        report.say(f"restored {info['file']} to {targets[role]}")
    live = {role: {**info, "file": targets[role].name} for role, info in manifest["files"].items()}
    check_copy({**manifest, "files": live}, targets["app"].parent, report, metadata=metadata, checksums=False) \
        if targets["app"].parent == targets["audit"].parent else _check_apart(manifest, targets, report, metadata)
    return report


def _check_apart(manifest: dict, targets: dict[str, Path], report: Report, metadata: MetaData | None) -> None:
    """The same checks when the two databases live in different folders."""
    with tempfile.TemporaryDirectory() as tmp:
        for role, info in manifest["files"].items():
            shutil.copy2(targets[role], Path(tmp) / info["file"])
        check_copy(manifest, Path(tmp), report, metadata=metadata, checksums=False)


def lines(reports: Iterable[Report]) -> str:
    return "\n".join(line for r in reports for line in r.lines)
