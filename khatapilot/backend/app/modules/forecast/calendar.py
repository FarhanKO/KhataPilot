"""Seasonal effects on a shop's sales: Ramadan, the days before Eid, and Eid closures.

These are stated assumptions (moon-sighting dates are approximate), not learned values: they are
the effects any shop owner in Bangladesh plans around. The forecast divides history by them to get
an everyday baseline, then multiplies them back into the days ahead.
"""

from __future__ import annotations

from datetime import date, timedelta

RAMADAN = [(date(2026, 2, 18), date(2026, 3, 19)), (date(2027, 2, 8), date(2027, 3, 9))]
EID_FITR = [date(2026, 3, 20), date(2027, 3, 10)]
EID_ADHA = [date(2026, 5, 27), date(2027, 5, 17)]

RAMADAN_FACTOR = {"grocery": 1.2, "tea_stall": 0.6}
PRE_EID_FACTOR = {"grocery": 1.3, "mobile_recharge": 1.35}


def sales_factor(shop_type: str, day: date) -> tuple[float, str | None]:
    """Multiplier on an ordinary day's sales, and the reason (None for an ordinary day)."""
    for eid in EID_FITR + EID_ADHA:
        if day == eid:
            return 0.2, "Eid (most shops close)"
        if day == eid + timedelta(days=1):
            return 0.5, "the day after Eid"
        if day == eid + timedelta(days=2):
            return 0.8, "Eid holidays"
    for eid in EID_FITR:
        if 1 <= (eid - day).days <= 7 and shop_type in PRE_EID_FACTOR:
            return PRE_EID_FACTOR[shop_type], "the week before Eid"
    for eid in EID_ADHA:
        if 1 <= (eid - day).days <= 5 and shop_type in PRE_EID_FACTOR:
            return PRE_EID_FACTOR[shop_type], "the days before Eid-ul-Adha"
    for start, end in RAMADAN:
        if start <= day <= end and shop_type in RAMADAN_FACTOR:
            return RAMADAN_FACTOR[shop_type], "Ramadan"
    return 1.0, None
