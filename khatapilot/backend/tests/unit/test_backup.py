"""Backups: made consistently, checked, rehearsed and restored, and a tampered copy is caught."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from app.bootstrap import register_models
from app.core.clock import SystemClock
from app.infra import backup
from app.infra.audit_log import AuditLog, AuditRecord
from app.infra.db import Base, Database
from app.modules.savings.models import SavingsPlan


def _record(n: int) -> AuditRecord:
    return AuditRecord(request_id=f"req_{n}", channel="test", actor="owner", provenance="owner", event="system",
                       summary=f"entry {n}", decision="INFO", reason="test")


@pytest.fixture
def live(tmp_path):
    register_models()
    app_url = f"sqlite:///{(tmp_path / 'live' / 'khatapilot.db').as_posix()}"
    audit_url = f"sqlite:///{(tmp_path / 'live' / 'audit.db').as_posix()}"
    db = Database(app_url)
    db.create_all()
    audit = AuditLog(audit_url, SystemClock())
    with db.session() as session:
        session.add(SavingsPlan(shop_id="shop-a", share_pct=10))
    for n in range(3):
        audit.append(_record(n))
    yield {"app_url": app_url, "audit_url": audit_url, "db": db, "audit": audit, "root": tmp_path / "backups"}
    db.dispose()
    audit.dispose()


def _make(live, **kw):
    return backup.create(app_url=live["app_url"], audit_url=live["audit_url"], root=live["root"], **kw)


def test_a_backup_is_checked_when_made_and_verifies_later(live):
    report = _make(live)
    assert report.ok
    manifest = backup.find(live["root"], None)
    assert manifest["files"]["app"]["tables"]["savings_plans"] == 1
    assert manifest["audit"]["entries"] == 3
    again = backup.verify(live["root"], metadata=Base.metadata)
    assert again.ok, again.lines


def test_a_drill_restores_into_a_scratch_folder_and_the_code_can_read_it(live, tmp_path):
    _make(live)
    report = backup.drill(live["root"], metadata=Base.metadata, scratch=tmp_path)
    assert report.ok, report.lines
    assert any("every table the current code uses can be read" in line for line in report.lines)


def test_a_restore_brings_back_the_backup_and_can_itself_be_undone(live):
    first = _make(live, name="before")
    with live["db"].session() as session:
        session.get(SavingsPlan, "shop-a").share_pct = 25
        session.add(SavingsPlan(shop_id="shop-b", share_pct=5))
    live["audit"].append(_record(99))
    live["db"].dispose()
    live["audit"].dispose()

    report = backup.restore(app_url=live["app_url"], audit_url=live["audit_url"], root=live["root"],
                            name=first.name, metadata=Base.metadata)
    assert report.ok, report.lines
    db = Database(live["app_url"])
    with db.session() as session:
        assert session.get(SavingsPlan, "shop-a").share_pct == 10
        assert session.get(SavingsPlan, "shop-b") is None
    db.dispose()
    audit = AuditLog(live["audit_url"], SystemClock())
    assert audit.verify().ok and audit.verify().entries == 3
    audit.dispose()

    safety = next(b for b in backup.backups(live["root"]) if b["folder"].name.startswith(backup.PRE_RESTORE))
    assert safety["files"]["app"]["tables"]["savings_plans"] == 2  # what was there before the restore
    undo = backup.restore(app_url=live["app_url"], audit_url=live["audit_url"], root=live["root"],
                          name=safety["folder"].name, metadata=Base.metadata)
    assert undo.ok, undo.lines
    db = Database(live["app_url"])
    with db.session() as session:
        assert session.get(SavingsPlan, "shop-a").share_pct == 25
    db.dispose()


def test_a_changed_backup_fails_its_checks(live):
    made = _make(live)
    audit_copy = made.folder / "audit.db"
    connection = sqlite3.connect(audit_copy)
    connection.execute("DROP TRIGGER audit_no_update")
    connection.execute("UPDATE audit_log SET summary = 'paid nobody' WHERE seq = 2")
    connection.commit()
    connection.close()
    report = backup.verify(live["root"])
    assert not report.ok
    text = "\n".join(report.lines)
    assert "checksum DOES NOT match" in text and "audit chain broken" in text


def test_only_the_newest_are_kept_but_safety_copies_stay(live):
    start = datetime(2026, 11, 1, tzinfo=UTC)
    for day in range(4):
        _make(live, keep=2, now=start + timedelta(days=day))
    _make(live, keep=2, now=start + timedelta(days=5), name=f"{backup.PRE_RESTORE}x")
    _make(live, keep=2, now=start + timedelta(days=6))
    names = [b["folder"].name for b in backup.backups(live["root"])]
    assert names[:1] == ["20261107-000000"] and f"{backup.PRE_RESTORE}x" in names
    assert len([n for n in names if not n.startswith(backup.PRE_RESTORE)]) == 2


def test_a_server_database_is_left_to_its_own_tools(live):
    with pytest.raises(backup.BackupError, match="pg_dump"):
        backup.create(app_url="postgresql://localhost/kp", audit_url=live["audit_url"], root=live["root"])
