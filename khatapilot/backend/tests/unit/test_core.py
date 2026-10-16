from datetime import UTC, date, datetime, timedelta

import pytest

from app.core.clock import BDT, SimClock, local_date, local_day_bounds
from app.core.ids import new_id, use_deterministic_ids
from app.core.masking import find_phone_numbers, mask_phone, mask_text, normalize_msisdn
from app.core.money import find_amounts, fmt_taka, parse_amount, to_paisa


class TestMoney:
    @pytest.mark.parametrize(
        ("raw", "paisa"),
        [
            ("1500", 150_000),
            ("1,500.00", 150_000),
            ("1,500.50", 150_050),
            ("১৫০০", 150_000),
            ("12,50,000", 125_000_000),
            (8000, 800_000),
        ],
    )
    def test_to_paisa(self, raw, paisa):
        assert to_paisa(raw) == paisa

    def test_to_paisa_rejects_text(self):
        with pytest.raises(ValueError):
            to_paisa("rice")

    @pytest.mark.parametrize(
        ("paisa", "text"),
        [(1_250_000, "৳12,500"), (150_050, "৳1,500.50"), (-200_000, "-৳2,000"), (0, "৳0")],
    )
    def test_fmt_taka(self, paisa, text):
        assert fmt_taka(paisa) == text

    @pytest.mark.parametrize(
        ("token", "paisa"),
        [
            ("8k", 800_000),
            ("8 hajar", 800_000),
            ("৮ হাজার", 800_000),
            ("1.5 lakh", 15_000_000),
            ("8,000", 800_000),
            ("rice", None),
        ],
    )
    def test_parse_amount(self, token, paisa):
        assert parse_amount(token) == paisa

    def test_find_amounts_skips_phone_numbers(self):
        assert find_amounts("send 50,000 to 01712345678 now") == [5_000_000]


class TestMasking:
    @pytest.mark.parametrize(
        "raw", ["01712345678", "+8801712345678", "8801712345678", "017-1234-5678", "০১৭১২৩৪৫৬৭৮"]
    )
    def test_normalize_msisdn(self, raw):
        assert normalize_msisdn(raw) == "01712345678"

    def test_normalize_rejects_non_mobile(self):
        assert normalize_msisdn("02-9876543") is None

    def test_mask_phone(self):
        assert mask_phone("01712345678") == "017XXXX5678"

    def test_mask_text_masks_every_number(self):
        text = "send 50000 to 01712345678 or +8801898765432"
        assert mask_text(text) == "send 50000 to 017XXXX5678 or 018XXXX5432"

    def test_find_phone_numbers(self):
        assert find_phone_numbers("call 01712345678, then 01898765432") == ["01712345678", "01898765432"]


class TestClock:
    def test_frozen_clock(self):
        start = datetime(2026, 10, 12, 10, 0, tzinfo=BDT)
        clock = SimClock(start, frozen=True)
        assert clock.now() == start
        clock.advance(timedelta(days=4))
        assert local_date(clock.now()) == date(2026, 10, 16)

    def test_naive_datetimes_are_dhaka_time(self):
        clock = SimClock(datetime(2026, 10, 12, 23, 30), frozen=True)
        assert clock.now().astimezone(UTC).hour == 17

    def test_local_day_bounds(self):
        start, end = local_day_bounds(date(2026, 10, 12))
        assert start == datetime(2026, 10, 11, 18, 0, tzinfo=UTC)
        assert end - start == timedelta(days=1)


def test_deterministic_ids():
    use_deterministic_ids()
    assert new_id("pay") == "pay_000001"
    assert new_id("pay") == "pay_000002"


def test_numbers_are_masked_so_the_owner_can_still_check_them():
    from app.core.masking import mask_number

    assert mask_number("01712345678") == "017XXXX5678"
    assert mask_number("2051334007812") == "****7812"  # a bank account keeps its last four digits
