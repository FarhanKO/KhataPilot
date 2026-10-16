import hashlib
import json
from datetime import date

import pandas as pd
import pytest

from data.generate_shops import EID_ADHA, EID_FITR, METHODS, RAMADAN, generate, season_factor
from data.hero_shop import HERO_SHOP_ID

FILES = ("shops.csv", "daily.csv", "hero_ledger.csv", "hero_parties.json", "hero_obligations.json")


def digest(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_same_seed_gives_identical_files(tmp_path):
    generate(n_shops=6, seed=7, out_dir=tmp_path / "a")
    generate(n_shops=6, seed=7, out_dir=tmp_path / "b")
    for name in FILES:
        assert digest(tmp_path / "a" / name) == digest(tmp_path / "b" / name), name


def test_another_seed_gives_another_population(tmp_path):
    generate(n_shops=6, seed=7, out_dir=tmp_path / "a")
    generate(n_shops=6, seed=8, out_dir=tmp_path / "b")
    assert digest(tmp_path / "a" / "shops.csv") != digest(tmp_path / "b" / "shops.csv")


def test_population_shape(generated_dir):
    shops = pd.read_csv(generated_dir / "shops.csv")
    train = shops[shops.is_hero == 0]
    assert len(train) >= 50
    assert train.history_days.between(183, 365).all(), "6 to 12 months of history"
    assert set(train.shop_type) == {"grocery", "pharmacy", "mobile_recharge", "tea_stall"}
    assert set(train.urbanicity) == {"urban", "semi_urban", "rural"}
    assert 0.08 <= train.defaulted.mean() <= 0.30


def test_every_shops_books_balance_and_never_go_negative(generated_dir):
    daily = pd.read_csv(generated_dir / "daily.csv")
    assert (daily.sales_total == daily.sales_cash + daily.sales_mobile + daily.sales_baki).all()
    for method in METHODS:
        assert (daily[f"balance_{method}"] >= 0).all(), method
    for shop_id, frame in daily.groupby("shop_id"):
        frame = frame.sort_values("date")
        flows = (frame.sales_cash + frame.sales_mobile + frame.baki_collected + frame.informal_borrowed
                 - frame.supplier_paid - frame.expenses - frame.drawings - frame.informal_repaid)
        drift = frame.closing_balance.diff().iloc[1:] - flows.iloc[1:]
        assert (drift == 0).all(), shop_id


def test_calendar_effects():
    assert season_factor("grocery", date(2026, 3, 1)) > 1.2  # Ramadan
    assert season_factor("grocery", EID_FITR) < 0.2  # shops close on Eid
    assert season_factor("tea_stall", date(2026, 3, 1)) < 1.0  # daytime fasting
    assert season_factor("grocery", EID_ADHA - pd.Timedelta(days=2).to_pytimedelta()) > 1.2


def test_ramadan_lifts_grocery_sales(generated_dir):
    daily = pd.read_csv(generated_dir / "daily.csv", parse_dates=["date"])
    shops = pd.read_csv(generated_dir / "shops.csv").set_index("shop_id")
    grocery = daily[daily.shop_id.map(shops.shop_type) == "grocery"]
    in_ramadan = grocery[(grocery.date >= str(RAMADAN[0])) & (grocery.date <= str(RAMADAN[1]))]
    before = grocery[(grocery.date >= "2026-01-15") & (grocery.date < str(RAMADAN[0]))]
    common = set(in_ramadan.shop_id) & set(before.shop_id)
    ratio = (in_ramadan[in_ramadan.shop_id.isin(common)].groupby("shop_id").sales_total.mean()
             / before[before.shop_id.isin(common)].groupby("shop_id").sales_total.mean())
    assert ratio.median() > 1.1


class TestHeroShop:
    @pytest.fixture
    def ledger(self, generated_dir):
        frame = pd.read_csv(generated_dir / "hero_ledger.csv", dtype={"counterparty_number": str})
        frame["day"] = frame.occurred_at.str[:10]
        return frame

    def test_profile(self, generated_dir):
        shops = pd.read_csv(generated_dir / "shops.csv")
        hero = shops[shops.shop_id == HERO_SHOP_ID].iloc[0]
        assert (hero["name"], hero["area"], hero["shop_type"]) == ("Rahman Store", "Mohakhali", "grocery")
        assert pd.isna(hero["defaulted"]), "the hero's label is what the credit model predicts"

    def test_ledger_reproduces_the_daily_books(self, generated_dir, ledger):
        daily = pd.read_csv(generated_dir / "daily.csv")
        books = daily[daily.shop_id == HERO_SHOP_ID].set_index("date")
        assert len(ledger) > 5000

        def per_day(kind):
            return ledger[ledger.kind == kind].groupby("day").amount.sum().reindex(books.index, fill_value=0)

        assert (per_day("sale") == books.sales_cash + books.sales_mobile).all()
        assert (per_day("baki_sale") == books.sales_baki).all()
        assert (per_day("baki_repayment") == books.baki_collected).all()

    def test_ledger_balances_match_the_end_state(self, generated_dir, ledger):
        end = json.loads((generated_dir / "hero_obligations.json").read_text(encoding="utf-8"))["balances"]
        signed = ledger.amount.where(ledger.direction == "in", -ledger.amount)
        by_method = signed.groupby(ledger.method).sum()
        for method in METHODS:
            assert by_method.get(method, 0) == end[method], method

    def test_suppliers_and_bills_for_the_demo(self, generated_dir):
        parties = json.loads((generated_dir / "hero_parties.json").read_text(encoding="utf-8"))
        names = {s["name"] for s in parties["suppliers"]}
        assert {"Rahim Traders", "Meghna Distributors", "Bismillah Oil Agency"} <= names
        obligations = json.loads((generated_dir / "hero_obligations.json").read_text(encoding="utf-8"))
        assert any(b["name"] == "Shop rent" for b in obligations["upcoming_bills"])
        assert obligations["supplier_payables"], "open supplier credit drives the cash forecast"
        assert sum(b["amount"] for b in obligations["open_baki"]) > 0
