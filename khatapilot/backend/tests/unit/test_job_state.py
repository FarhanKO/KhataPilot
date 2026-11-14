"""One scheduler at a time, and once-a-period checks that alert once, even across processes."""

from datetime import timedelta

from app.bootstrap import register_models
from app.infra import job_state as J
from app.infra.db import Database


def database(tmp_path) -> Database:
    register_models()
    db = Database(f"sqlite:///{tmp_path / 'jobs.db'}")
    db.create_all()
    return db


def test_only_one_process_holds_the_scheduler_lease(tmp_path):
    db = database(tmp_path)
    with db.session() as s:
        assert J.acquire_lease(s, "scheduler", "worker-a", timedelta(seconds=30))
    with db.session() as s:
        assert not J.acquire_lease(s, "scheduler", "worker-b", timedelta(seconds=30))  # a holds a live lease
    with db.session() as s:
        assert J.acquire_lease(s, "scheduler", "worker-a", timedelta(seconds=30))  # renewing is fine
    with db.session() as s:
        J.release_lease(s, "scheduler", "worker-a")
    with db.session() as s:
        assert J.acquire_lease(s, "scheduler", "worker-b", timedelta(seconds=30))


def test_a_lapsed_lease_can_be_taken_over(tmp_path):
    db = database(tmp_path)
    with db.session() as s:
        assert J.acquire_lease(s, "scheduler", "worker-a", timedelta(seconds=-1))  # already expired
    with db.session() as s:
        assert J.acquire_lease(s, "scheduler", "worker-b", timedelta(seconds=30))


def test_a_mark_is_recorded_once(tmp_path):
    db = database(tmp_path)
    with db.session() as s:
        assert J.mark_once(s, "savings-202610")
        assert not J.mark_once(s, "savings-202610")
        assert J.is_marked(s, "savings-202610") and not J.is_marked(s, "savings-202611")
    with db.session() as s:  # a second process (another session) sees it too
        assert not J.mark_once(s, "savings-202610")
