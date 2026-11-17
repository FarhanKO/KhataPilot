"""A database made by an older version gains the columns added since, so the owner's books keep working
after an update (no migration step to forget)."""

from datetime import datetime

from sqlalchemy import inspect, text

from app.bootstrap import register_models
from app.core.clock import BDT, SimClock
from app.infra.db import Database
from app.modules.savings.service import SavingsService


NOW = datetime(2026, 10, 12, 10, tzinfo=BDT)


def test_an_older_database_gains_new_columns_and_keeps_its_rows(tmp_path):
    register_models()
    url = f"sqlite:///{(tmp_path / 'old.db').as_posix()}"
    old = Database(url)
    with old.engine.begin() as connection:  # savings_plans as it was before the stock share existed
        connection.execute(text(
            "CREATE TABLE savings_plans (shop_id VARCHAR(64) PRIMARY KEY, share_pct INTEGER, share_set_at DATETIME, "
            "account_name VARCHAR(64), account_number VARCHAR(32), account_method VARCHAR(8), account_set_at DATETIME)"))
        connection.execute(text("INSERT INTO savings_plans (shop_id, share_pct) VALUES ('rahman-store', 10)"))
    old.dispose()

    db = Database(url)
    db.create_all()
    columns = {c["name"] for c in inspect(db.engine).get_columns("savings_plans")}
    assert {"basis", "reinvest_pct", "reinvest_set_at"} <= columns
    with db.session() as session:
        savings = SavingsService(session, SimClock(NOW), "rahman-store", standard_buffer_paisa=500_000)
        assert savings.share() == 10 and savings.basis() == "last_month" and savings.reinvest_share() is None
        savings.set_reinvest_share(15)
    with db.session() as session:
        assert SavingsService(session, SimClock(NOW), "rahman-store", standard_buffer_paisa=500_000).reinvest_share() == 15
