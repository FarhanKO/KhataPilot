"""The safety buffer: the least the owner wants on hand, below which the forecast warns.

One fixed amount does not fit every shop: ৳5,000 is a cushion for a tea stall and nothing for a
wholesaler. So unless the owner chooses their own (in chat), it is sized from the shop's own books:

- size: money in on a usual day (sales in cash and mobile money, plus baki coming back);
- up-and-down: how far a day typically lands from the usual for that weekday (the forecast's own
  day-to-day spread, so the weekly pattern it already plans for is not counted as risk).

The buffer covers three worse-than-usual days in a row, 19 times in 20 (the classic safety-stock
rule: 1.645 x daily swing x sqrt(3)). Three days is about how long it takes to collect baki or
talk a supplier into waiting. However steady the shop, it is never less than a quarter of a usual
day's money in, so a big shop always keeps a cushion in proportion to its size.
"""

from __future__ import annotations

import math
from datetime import datetime

from app.core.clock import local_date
from app.core.money import fmt_taka
from app.modules.forecast.schemas import SafetyBuffer

Z_95 = 1.645
COVER_DAYS = 3
SIZE_FLOOR_SHARE = 0.25  # at least a quarter of a usual day's money in
MIN_BUFFER_PAISA = 1_000_00
ROUND_TO_PAISA = 500_00
MIN_HISTORY_DAYS = 14  # trading days needed before the books can size it
STEADY_BELOW, CHOPPY_FROM = 0.10, 0.25
STEADINESS_WORDS = {
    "steady": "is steady",
    "moderate": "goes up and down a fair bit",
    "choppy": "goes up and down a lot",
}


def _round_up(paisa: float) -> int:
    return int(math.ceil(paisa / ROUND_TO_PAISA)) * ROUND_TO_PAISA


def _whole(paisa: float) -> int:
    return int(round(paisa / 100)) * 100


def steadiness(ratio: float) -> str:
    return "steady" if ratio < STEADY_BELOW else "choppy" if ratio >= CHOPPY_FROM else "moderate"


def size_buffer(*, daily_in_paisa: float, swing_paisa: float, trading_days: int,
                standard_paisa: int) -> SafetyBuffer:
    """The automatic buffer for a shop of this size and swing (pure, so it is easy to test)."""
    if trading_days < MIN_HISTORY_DAYS or daily_in_paisa <= 0:
        return SafetyBuffer(
            amount_paisa=standard_paisa, mode="standard", auto_paisa=None, daily_in_paisa=None, swing_paisa=None,
            swing_ratio=None, steadiness=None,
            explanation=(f"Only {trading_days} days of sales in the books so far, too few to size the buffer from "
                         f"your shop, so the standard {fmt_taka(standard_paisa)} is used. Tell me in chat if you want "
                         "a different amount."))
    ratio = swing_paisa / daily_in_paisa
    from_swing = Z_95 * swing_paisa * math.sqrt(COVER_DAYS)
    from_size = SIZE_FLOOR_SHARE * daily_in_paisa
    amount = max(MIN_BUFFER_PAISA, _round_up(max(from_swing, from_size)))
    level = steadiness(ratio)
    size_text = (f"About {fmt_taka(_whole(daily_in_paisa))} comes in on a usual day, and it "
                 f"{STEADINESS_WORDS[level]}: a typical day lands about {fmt_taka(_whole(swing_paisa))} "
                 f"({ratio:.0%}) away from the usual for that weekday.")
    if from_swing >= from_size:
        why = f" {fmt_taka(amount)} covers three worse-than-usual days in a row, 19 times in 20."
    else:
        why = f" Because your takings are steady, the buffer is a quarter of a usual day's money in: {fmt_taka(amount)}."
    return SafetyBuffer(
        amount_paisa=amount, mode="auto", auto_paisa=amount, daily_in_paisa=_whole(daily_in_paisa),
        swing_paisa=_whole(swing_paisa), swing_ratio=round(ratio, 3), steadiness=level,
        explanation=f"Set automatically from your books. {size_text}{why} Tell me in chat if you want a different amount.")


def owner_buffer(amount_paisa: int, set_at: datetime, auto: SafetyBuffer) -> SafetyBuffer:
    """The owner's own choice, with what the automatic one would be, so they can compare."""
    if auto.mode == "auto":
        compare = f" The automatic buffer for your shop would be {fmt_taka(auto.amount_paisa)}."
    else:
        compare = f" There is not enough history yet to size one automatically (standard {fmt_taka(auto.amount_paisa)})."
    return auto.model_copy(update={
        "amount_paisa": amount_paisa, "mode": "owner", "set_at": set_at,
        "explanation": (f"You set this to {fmt_taka(amount_paisa)} on {local_date(set_at):%d %b}.{compare} Say \"use the "
                        "automatic buffer\" to go back to it."),
    })
