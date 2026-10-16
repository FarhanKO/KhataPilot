"""Credit profile: features, the model's guarantees, explanations, fairness helpers and consent."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.bootstrap import register_models
from app.infra.db import Database
from app.infra.model_files import ModelIntegrityError
from app.modules.credit import calibration, explain, fairness
from app.modules.credit.features import COLUMNS, MIN_HISTORY_DAYS, NAMES, SCORED, compute, vector
from app.modules.credit.model import CreditModel, band_for
from app.modules.credit.schemas import ConsentRequired
from app.modules.credit.service import CreditService
from app.modules.ledger.service import LedgerService
from scripts.seed import seed_backend

SHOP = "rahman-store"


def table(days: int = 180, **columns) -> dict[str, np.ndarray]:
    base = {c: np.zeros(days) for c in COLUMNS}
    base.update({"sales_cash": np.full(days, 600.0), "sales_mobile": np.full(days, 300.0),
                 "sales_baki": np.full(days, 100.0), "baki_collected": np.full(days, 90.0),
                 "purchases": np.full(days, 850.0), "expenses": np.full(days, 50.0),
                 "drawings": np.full(days, 40.0), "closing_balance": np.full(days, 9_000.0)})
    base.update(columns)
    return base


# ---------------------------------------------------------------- features

def test_features_of_a_steady_shop_can_be_checked_by_hand():
    on_time = np.zeros(180)
    on_time[::20] = 1  # 9 supplier payments on time
    late = np.zeros(180)
    late[5] = 1  # and one late
    borrowed = np.zeros(180)
    borrowed[[10, 50]] = 2_000
    f = compute(table(supplier_payments_on_time=on_time, supplier_payments_late=late, informal_borrowed=borrowed,
                      baki_outstanding=np.full(180, 1_500.0)), history_days=304)
    assert f["avg_daily_sales"] == 1_000
    assert f["sales_volatility"] == 0 and f["sales_growth"] == pytest.approx(0, abs=1e-9)
    assert f["gross_margin"] == pytest.approx(0.15)
    assert f["cash_buffer_days"] == pytest.approx(9_000 / 900)
    assert f["baki_collection_rate"] == pytest.approx(0.9)
    assert f["baki_days_outstanding"] == pytest.approx(15)  # 1,500 outstanding / 100 given a day
    assert f["supplier_on_time"] == pytest.approx(0.9)
    assert f["mobile_money_share"] == pytest.approx(1 / 3)
    assert f["months_of_history"] == 10.0
    assert f["drawings_share"] == pytest.approx(0.04)
    assert f["informal_borrowing"] == 2


def test_growth_is_measured_per_year():
    sales = 1_000 * np.exp(np.log(1.5) / 365 * np.arange(180))  # +50% a year
    f = compute(table(sales_cash=sales, sales_mobile=np.zeros(180), sales_baki=np.zeros(180)))
    assert f["sales_growth"] == pytest.approx(0.5, abs=0.02)


def test_too_little_history_is_refused():
    with pytest.raises(ValueError, match="90 days"):
        compute(table(days=MIN_HISTORY_DAYS - 1))


def test_the_app_and_the_training_data_compute_the_same_features(tmp_path, clock, generated_dir):
    """The demo shop's ledger, read through the app, gives exactly the features its daily.csv rows give."""
    register_models()
    db = Database(f"sqlite:///{(tmp_path / 'b.db').as_posix()}")
    db.create_all()
    seed_backend(db, clock, SHOP, generated_dir)
    daily = pd.read_csv(generated_dir / "daily.csv")
    hero = daily[daily.shop_id == SHOP].sort_values("date")
    first, last = date.fromisoformat(hero["date"].iloc[0]), date.fromisoformat(hero["date"].iloc[-1])
    with db.session() as session:
        books = LedgerService(session, clock).book_days(SHOP, first, last)
    db.dispose()
    from app.modules.credit.features import from_books

    assert from_books(books) == pytest.approx(compute({c: hero[c].to_numpy() for c in COLUMNS}))


# ---------------------------------------------------------------- the model

@pytest.fixture(scope="module")
def model(credit_model_path) -> CreditModel:
    return CreditModel.load(credit_model_path)


def test_bands():
    assert [band_for(p)[0] for p in (0.01, 0.05, 0.09, 0.19, 0.2, 0.34, 0.6)] == ["A", "B", "B", "C", "D", "D", "E"]


def test_shop_type_and_region_are_never_inputs(model):
    assert model.features == [f.name for f in SCORED]
    assert not {"shop_type", "urbanicity", "division", "district", "area"} & set(model.features)
    assert {"avg_daily_sales", "gross_margin", "mobile_money_share"}.isdisjoint(model.features)


def test_paying_suppliers_on_time_never_raises_the_risk(model):
    """Monotone constraints: along each behavioural feature, the risk moves only one way."""
    shop = {n: model.peers[n]["median"] for n in NAMES}
    for name, direction in (("supplier_on_time", -1), ("baki_collection_rate", -1), ("cash_buffer_days", -1),
                            ("sales_volatility", +1), ("drawings_share", +1), ("informal_borrowing", +1)):
        low, high = model.peers[name]["p25"], model.peers[name]["p75"] * 1.5 + 1e-6
        grid = np.linspace(low, high, 15)
        risk = [model.probability(vector({**shop, name: v}, model.features))[0] for v in grid]
        steps = np.diff(risk) * direction
        assert (steps >= -1e-12).all(), name


def test_shap_values_add_up_to_the_score(model):
    shop = {n: model.peers[n]["p25"] for n in NAMES}
    x = vector(shop, model.features)
    contributions, base = model.contributions(x)
    assert sum(contributions.values()) + base == pytest.approx(model.raw(x)[0])


def test_a_tampered_model_file_is_refused(model, tmp_path):
    path = tmp_path / "credit.joblib"
    model.save(path)
    path.write_bytes(path.read_bytes() + b"\0")
    with pytest.raises(ModelIntegrityError, match="checksum"):
        CreditModel.load(path)


# ---------------------------------------------------------------- calibration, fairness, explanations

def test_calibration_helpers():
    y = np.array([0, 0, 1, 1])
    assert calibration.brier(np.array([0.0, 0.0, 1.0, 1.0]), y) == 0
    assert calibration.brier(np.full(4, 0.5), y) == 0.25
    curve = calibration.reliability(np.array([0.1, 0.2, 0.8, 0.9]), y, bins=2)
    assert curve == [{"predicted": pytest.approx(0.15), "observed": 0.0, "shops": 2},
                     {"predicted": pytest.approx(0.85), "observed": 1.0, "shops": 2}]
    a, b = calibration.fit_platt(np.array([-2.0, -1.0, 1.0, 2.0, -1.5, 1.5]), np.array([0, 0, 1, 1, 0, 1]))
    assert a > 0


def test_wilson_interval_is_wide_for_small_groups():
    low, high = fairness.wilson(3, 25)
    assert low < 0.12 < high and high - low > 0.2
    low, high = fairness.wilson(300, 2_500)
    assert high - low < 0.03


def test_reasons_are_split_by_the_sign_of_their_shap_value():
    features = {"supplier_on_time": 0.94, "sales_volatility": 0.30, "drawings_share": 0.06, "sales_growth": 0.03,
                "informal_borrowing": 0.0}
    peers = {n: {"median": m} for n, m in (("supplier_on_time", 0.77), ("sales_volatility", 0.11),
                                           ("drawings_share", 0.08), ("sales_growth", 0.17),
                                           ("informal_borrowing", 0.0))}
    contributions = {"supplier_on_time": -0.3, "sales_volatility": 0.5, "drawings_share": -0.1,
                     "sales_growth": 0.02, "informal_borrowing": -0.06}
    helps, hurts = explain.reasons(features, contributions, peers)
    assert [r.feature for r in helps] == ["supplier_on_time", "drawings_share", "informal_borrowing"]
    assert [r.feature for r in hurts] == ["sales_volatility"]  # growth's push is too small to mention
    assert helps[0].text == "You pay supplier bills on time 94% of the time, more often than most shops (77%)."
    assert hurts[0].text == ("Your weekly sales swing by about 30% from week to week, less steady than most shops "
                             "(11%).")
    assert helps[2].text == "You have not had to borrow from relatives to pay bills in the last 6 months."


# ---------------------------------------------------------------- consent and the service

@pytest.fixture
def hero_db(tmp_path, clock, generated_dir):
    register_models()
    db = Database(f"sqlite:///{(tmp_path / 'hero.db').as_posix()}")
    db.create_all()
    seed_backend(db, clock, SHOP, generated_dir)
    yield db
    db.dispose()


def test_nothing_is_built_without_consent(hero_db, clock, model):
    with hero_db.session() as session:
        service = CreditService(session, clock, SHOP, model)
        assert not service.consent().granted
        with pytest.raises(ConsentRequired):
            service.build()
        assert service.latest() is None


def test_the_profile_explains_itself_and_withdrawing_consent_deletes_it(hero_db, clock, model):
    with hero_db.session() as session:
        service = CreditService(session, clock, SHOP, model)
        service.grant_consent()
        profile = service.build()
        assert profile.band in "ABCDE" and 0 < profile.probability_of_default < 1
        assert profile.summary.startswith(f"Band {profile.band} (")
        assert profile.helps and all(r.direction == "helps" and r.impact < 0 for r in profile.helps)
        assert all(r.direction == "hurts" and r.impact > 0 for r in profile.hurts)
        shown = {f.name: f.scored for f in profile.features}
        assert shown["avg_daily_sales"] is False and shown["supplier_on_time"] is True
        assert profile.history_days >= 300
        assert service.latest().id == profile.id
        assert service.withdraw_consent() == 1
        assert service.latest() is None and not service.consent().granted
        assert service.get(profile.id) is None


def test_the_profile_uses_complete_days_only(hero_db, clock, model):
    """Today's half-finished day is left out, so the score does not move with every sale."""
    with hero_db.session() as session:
        service = CreditService(session, clock, SHOP, model)
        _, days = service.current_features()
    # The books run from 1 Nov 2025; "now" is Mon 12 Oct 2026 10:00, so the last complete day is Sun 11 Oct.
    assert days == (date(2026, 10, 11) - date(2025, 11, 1)).days + 1
