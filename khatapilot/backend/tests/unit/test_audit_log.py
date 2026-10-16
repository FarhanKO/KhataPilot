"""The audit log is append-only and tamper-evident."""

import sqlite3

import pytest
from sqlalchemy.exc import DatabaseError

from app.infra.audit_log import GENESIS_HASH, AuditLog, AuditRecord


@pytest.fixture
def audit(tmp_path, clock):
    log = AuditLog(f"sqlite:///{(tmp_path / 'audit.db').as_posix()}", clock)
    yield log
    log.dispose()


def record(n: int, **extra) -> AuditRecord:
    fields = dict(request_id=f"req-{n}", channel="web", actor="owner", provenance="owner", event="decision",
                  summary=f"entry {n}", decision="ALLOWED", reason="test")
    fields.update(extra)
    return AuditRecord(**fields)


def raw(tmp_path) -> sqlite3.Connection:
    return sqlite3.connect(tmp_path / "audit.db")


def test_entries_are_chained(audit):
    first = audit.append(record(1))
    second = audit.append(record(2))
    assert first.prev_hash == GENESIS_HASH
    assert second.prev_hash == first.hash
    report = audit.verify()
    assert report.ok and report.entries == 2 and report.head_hash == second.hash


def test_update_and_delete_are_refused_by_the_database(audit):
    audit.append(record(1))
    with audit.engine.begin() as connection, pytest.raises(DatabaseError, match="append-only"):
        connection.exec_driver_sql("UPDATE audit_log SET reason = 'nothing to see' WHERE seq = 1")
    with audit.engine.begin() as connection, pytest.raises(DatabaseError, match="append-only"):
        connection.exec_driver_sql("DELETE FROM audit_log WHERE seq = 1")


def test_a_modified_entry_breaks_the_chain(audit, tmp_path):
    for n in range(1, 5):
        audit.append(record(n))
    with raw(tmp_path) as connection:  # an attacker with file access drops the trigger first
        connection.execute("DROP TRIGGER audit_no_update")
        connection.execute("UPDATE audit_log SET decision = 'BLOCKED' WHERE seq = 3")
    report = audit.verify()
    assert not report.ok
    assert report.first_bad_seq == 3
    assert "modified" in report.problem


def test_a_deleted_entry_breaks_the_chain(audit, tmp_path):
    for n in range(1, 5):
        audit.append(record(n))
    with raw(tmp_path) as connection:
        connection.execute("DROP TRIGGER audit_no_delete")
        connection.execute("DELETE FROM audit_log WHERE seq = 2")
    report = audit.verify()
    assert not report.ok
    assert report.first_bad_seq == 3
    assert "link" in report.problem


def test_phone_numbers_are_masked_everywhere(audit):
    entry = audit.append(record(
        1, summary="Pay 01712345678 ৳8,000", reason="to +8801712345678",
        action={"payee_number": "01712345678", "amount_paisa": 13456789012},
        checks=[{"detail": "number 01999888777 is new"}],
    ))
    assert entry.summary == "Pay 017XXXX5678 ৳8,000"
    assert entry.reason == "to 017XXXX5678"
    assert entry.action == {"payee_number": "017XXXX5678", "amount_paisa": 13456789012}
    assert entry.checks == [{"detail": "number 019XXXX8777 is new"}]


def test_listeners_see_every_entry(audit):
    seen = []
    audit.subscribe(seen.append)
    audit.append(record(1))
    assert [e.summary for e in seen] == ["entry 1"]


def test_reset_starts_a_fresh_chain(audit):
    audit.append(record(1))
    audit.reset()
    assert audit.head() == GENESIS_HASH
    assert audit.append(record(2)).prev_hash == GENESIS_HASH
    assert audit.verify().ok
