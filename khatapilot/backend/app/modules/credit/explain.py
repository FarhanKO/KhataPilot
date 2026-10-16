"""SHAP contributions turned into plain sentences an owner (and a loan officer) can check.

Which reasons "help" or "hurt" comes from the sign of each feature's SHAP value; the sentence
states the shop's own number next to the typical shop's, so every reason can be verified against
the books.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.modules.credit.features import BY_NAME
from app.modules.credit.schemas import Reason

MIN_IMPACT = 0.05  # log-odds; smaller pushes are not worth an owner's attention


def _than(value: float, typical: float, lower: str, higher: str) -> str:
    if abs(value - typical) <= 0.05 * max(abs(typical), 1e-9):
        return "about the same as"
    return f"{lower} than" if value < typical else f"{higher} than"


def sentence(name: str, v: float, m: float) -> str:
    """One factual sentence about feature `name`, with value `v` and the typical shop's `m`."""
    if name == "supplier_on_time":
        return (f"You pay supplier bills on time {v:.0%} of the time, "
                f"{_than(v, m, 'less often', 'more often')} most shops ({m:.0%}).")
    if name == "sales_volatility":
        return (f"Your weekly sales swing by about {v:.0%} from week to week, "
                f"{_than(v, m, 'steadier', 'less steady')} most shops ({m:.0%}).")
    if name == "baki_days_outstanding":
        return (f"Customers repay your baki in about {v:.0f} days on average, "
                f"{_than(v, m, 'faster', 'slower')} most shops ({m:.0f} days).")
    if name == "baki_collection_rate":
        return (f"{min(v, 1):.0%} of the credit you give customers comes back, "
                f"{_than(v, m, 'less', 'more')} most shops ({min(m, 1):.0%}).")
    if name == "drawings_share":
        return (f"Household withdrawals take {v:.0%} of your sales, "
                f"{_than(v, m, 'less', 'more')} most shops ({m:.0%}).")
    if name == "months_of_history":
        return f"Your books cover {v:.0f} months, {_than(v, m, 'shorter', 'longer')} most shops ({m:.0f})."
    if name == "sales_growth":
        trend = (f"growing about {v:.0%} a year" if v > 0.02
                 else f"shrinking about {-v:.0%} a year" if v < -0.02 else "flat")
        return f"Your sales are {trend} (most shops: {m:+.0%} a year)."
    if name == "cash_buffer_days":
        return (f"You keep about {v:.1f} days of costs in cash, wallets and bank, "
                f"{_than(v, m, 'less', 'more')} most shops ({m:.1f} days).")
    if name == "informal_borrowing":
        if v < 1:
            return "You have not had to borrow from relatives to pay bills in the last 6 months."
        times = "once" if round(v) == 1 else f"{v:.0f} times"
        return f"You borrowed from relatives to pay bills {times} in the last 6 months."
    return f"{BY_NAME[name].label}: {v:.2f} (most shops: {m:.2f})."


def reasons(features: Mapping[str, float], contributions: Mapping[str, float],
            peers: Mapping[str, Mapping[str, float]], top: int = 3) -> tuple[list[Reason], list[Reason]]:
    """The top `top` reasons lowering the risk ("helps") and raising it ("hurts")."""
    items = []
    for name, impact in contributions.items():
        if abs(impact) < MIN_IMPACT:
            continue
        typical = peers[name]["median"]
        items.append(Reason(feature=name, label=BY_NAME[name].label, value=features[name], typical=typical,
                            impact=round(impact, 3), direction="helps" if impact < 0 else "hurts",
                            text=sentence(name, features[name], typical)))
    helps = sorted((r for r in items if r.direction == "helps"), key=lambda r: r.impact)[:top]
    hurts = sorted((r for r in items if r.direction == "hurts"), key=lambda r: -r.impact)[:top]
    return helps, hurts
