"""How much to save this month: pure functions, so every number is easy to test and explain.

- Profit is cash profit for a calendar month: money from sales (cash and mobile money) and baki coming
  back, minus what the shop paid for stock, bills and running costs. Household withdrawals and earlier
  savings are the owner's own money, not costs, so they never lower it.
- This month's savings are the owner's share of LAST month's profit: a finished month, so the target is
  a known number, not a guess. A good month saves more, a lean one less, a loss-making one nothing.
  An owner whose months jump around can choose the middle of the last three months instead (steadier).
- What moves today is capped by the cash forecast: a transfer today lowers every day ahead by the same
  amount, so the most that can go is the forecast's lowest balance minus the safety buffer.
- Once profit has grown for three months, KhataPilot suggests a bigger share (five points at a time, up
  to 30%), but only if the bigger amount still fits this month. It never changes the share by itself.
"""

from __future__ import annotations

import statistics
from datetime import date, timedelta

from app.core.money import fmt_taka
from app.modules.ledger.schemas import DailyFlow
from app.modules.savings.schemas import GoalDraft, SavingsJump, SavingsState, ScaleUp

ROUND_TO_PAISA = 100_00  # savings move in whole hundreds of taka
SCALE_STEP_PCT, SCALE_MAX_PCT = 5, 30
SCALE_GROWTH = 0.10  # profit up at least 10% (last three months against the three before)


def month_start(day: date) -> date:
    return day.replace(day=1)


def month_end(month: date) -> date:
    return (month.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def previous_month(month: date) -> date:
    return (month.replace(day=1) - timedelta(days=1)).replace(day=1)


def month_label(month: date) -> str:
    return f"{month:%b %Y}"


def round_down(paisa: int) -> int:
    return max(0, paisa) // ROUND_TO_PAISA * ROUND_TO_PAISA


def cash_profit(flows: dict[date, DailyFlow]) -> int:
    return sum(f.sales_in + f.baki_collected - f.routine_out - f.supplier_payments - f.bills_paid
               for f in flows.values())


def target_for(share_pct: int, last_profit_paisa: int) -> int:
    return round_down(last_profit_paisa * share_pct // 100)


def decide(*, share_pct: int | None, has_account: bool, last_profit: int, saved: int, pending: int,
           safe_now: int) -> tuple[SavingsState, int, int, int]:
    """(state, target, remaining, suggested) for this month."""
    if share_pct is None:
        return "no_plan", 0, 0, 0
    if last_profit <= 0:
        return "no_profit", 0, 0, 0
    target = target_for(share_pct, last_profit)
    remaining = max(0, target - saved - pending)
    if remaining < ROUND_TO_PAISA:
        return "done", target, 0, 0
    suggested = min(remaining, round_down(safe_now))
    if not has_account:
        return "no_account", target, remaining, suggested
    if suggested >= remaining:
        return "ready", target, remaining, remaining
    if suggested > 0:
        return "partial", target, remaining, suggested
    return "wait", target, remaining, 0


def basis_words(basis: str, last_month: date, profit: int) -> tuple[str, str]:
    """(what the target is a share of, what to say when there was no profit), in words."""
    if basis == "three_months":
        first = previous_month(previous_month(last_month))
        span = f"{first:%b}–{last_month:%b}"
        return (f"a usual month's {fmt_taka(profit)} profit (the middle of {span})",
                f"the middle of {span} was no profit ({fmt_taka(profit)})")
    return f"{last_month:%B}'s {fmt_taka(profit)} profit", f"{last_month:%B} made no profit ({fmt_taka(profit)})"


def steady_words(last_month: date, base: int) -> str:
    return f"a steady {fmt_taka(base)} (capped, as {last_month:%B} was far above a usual month)"


def basis_profit(basis: str, profits_before: list[int]) -> int:
    """The profit this month's target is a share of: last month, or the middle of the last three (steadier).
    With last month, a month far above the usual one is capped (see `steady_base`)."""
    if basis == "three_months":
        return int(statistics.median(profits_before[-3:]))
    return steady_base(profits_before)[0]


# A month counts as a jump when its profit is at least 1.6 times the usual one (the middle of the three months
# before it) or at most half of it. Up: the target is a share of at most 1.5 times the usual profit, so one Eid
# month or one big order does not suddenly ask the household for twice the usual savings. Down: the target
# follows the lower profit (saving less is always safe) and says why.
JUMP_UP, JUMP_DOWN, JUMP_CAP = 1.6, 0.5, 1.5


def steady_base(profits_before: list[int]) -> tuple[int, SavingsJump | None]:
    """(the profit to take a share of, the jump if last month was one). `profits_before`: oldest first."""
    last = profits_before[-1]
    if len(profits_before) < 4:
        return last, None
    usual = int(statistics.median(profits_before[-4:-1]))
    if usual <= 0 or last <= 0:
        return last, None
    if last >= usual * JUMP_UP:
        base = int(usual * JUMP_CAP)
        return base, SavingsJump(
            direction="up", last_profit_paisa=last, usual_profit_paisa=usual, base_paisa=base,
            text=(f"Last month's profit ({fmt_taka(last)}) was far above a usual month ({fmt_taka(usual)}), so this "
                  f"month's savings follow {fmt_taka(base)} (one and a half usual months), not the whole jump. If the "
                  "higher profit lasts, next month's target rises with it; a one-off month does not suddenly double "
                  "what the household has to put aside."))
    if last <= usual * JUMP_DOWN:
        return last, SavingsJump(
            direction="down", last_profit_paisa=last, usual_profit_paisa=usual, base_paisa=last,
            text=(f"Last month's profit ({fmt_taka(last)}) was well below a usual month ({fmt_taka(usual)}), so this "
                  "month's savings are smaller too. To keep the amount steadier from month to month, base it on a "
                  "usual month instead (the middle of the last three)."))
    return last, None


def headline(state: SavingsState, *, share_pct: int | None, basis_text: str, nothing_text: str, target: int,
             saved: int, pending: int, remaining: int, suggested: int, buffer: int, lowest: int,
             lowest_day: date) -> str:
    share = f"{share_pct}% of {basis_text}"
    if state == "no_plan":
        return ("No savings plan yet. Pick a share of each month's profit to put aside for yourself: a good month "
                "saves more, a lean month less, and never so much that cash drops below your safety buffer.")
    if state == "no_profit":
        return f"Nothing to save this month: {nothing_text}. Your plan picks up again next month."
    if state == "done":
        on_way = f" ({fmt_taka(pending)} of it still on its way)" if pending else ""
        return f"This month's savings are done: {fmt_taka(saved + pending)} of {fmt_taka(target)}{on_way}, {share}."
    if state == "no_account":
        return (f"This month's savings are {fmt_taka(target)}, {share}. Add the savings account the money should "
                "go to (with your PIN) to move it.")
    if state == "ready":
        return (f"{fmt_taka(remaining)} is ready to move to your savings: {share}. Cash stays above your "
                f"{fmt_taka(buffer)} safety buffer.")
    if state == "partial":
        return (f"Lean month: move {fmt_taka(suggested)} now, not the full {fmt_taka(remaining)} ({share}). More "
                f"would take cash below your {fmt_taka(buffer)} safety buffer around {lowest_day:%a %d %b}.")
    return (f"Hold this month's {fmt_taka(remaining)} for now ({share}): cash is forecast to dip to "
            f"{fmt_taka(lowest)} on {lowest_day:%a %d %b}, too close to your {fmt_taka(buffer)} safety buffer. "
            "I'll tell you when it is safe.")


def scale_up(*, share_pct: int | None, profits: list[int], safe_now: int, saved: int, pending: int) -> ScaleUp | None:
    """`profits`: the last six finished months, oldest first."""
    if share_pct is None or share_pct >= SCALE_MAX_PCT or len(profits) < 6:
        return None
    before, recent = statistics.mean(profits[:3]), statistics.mean(profits[3:])
    if before <= 0 or recent < before * (1 + SCALE_GROWTH) or min(profits[3:]) <= 0:
        return None
    to_pct = min(SCALE_MAX_PCT, share_pct + SCALE_STEP_PCT)
    new_target = target_for(to_pct, profits[-1])
    if new_target - saved - pending > round_down(safe_now):
        return None  # the bigger amount would not fit this month: suggest it when it does
    growth = round((recent / before - 1) * 100)
    return ScaleUp(
        from_pct=share_pct, to_pct=to_pct, growth_pct=growth, new_target_paisa=new_target,
        text=(f"Profit is up {growth}% over the last three months (about {fmt_taka(round_down(int(recent)))} a "
              f"month, against {fmt_taka(round_down(int(before)))} before). Saving {to_pct}% instead of "
              f"{share_pct}% would put aside {fmt_taka(new_target)} this month and still keep cash above your "
              "safety buffer."))


# ---------------------------------------------------------------- goals

GOAL_ROUND_PAISA = 1_000_00  # goal amounts are whole thousands of taka, rounded up


def round_up_goal(paisa: int) -> int:
    return -(-max(0, paisa) // GOAL_ROUND_PAISA) * GOAL_ROUND_PAISA


def month_totals(rows: list[tuple[str, str, str, int]]) -> dict[str, int]:
    """One month's category totals (kind, method, category, paisa) as running costs, household money
    taken out (savings excluded) and stock bought (cash or credit)."""
    costs = household = stock = 0
    for kind, _method, category, amount in rows:
        if kind in ("expense", "other_out"):
            costs += amount
        elif kind == "owner_drawing" and category != "Personal savings":
            household += amount
        elif kind == "purchase":
            stock += amount
    return {"costs": costs, "household": household, "stock": stock}


def round_down_goal(paisa: int) -> int:
    return max(0, paisa) // GOAL_ROUND_PAISA * GOAL_ROUND_PAISA


def starter_goals(months: list[dict[str, int]], *, stock_up: tuple[str, date] | None, pace: int,
                  this_month: date) -> list[GoalDraft]:
    """Starter goals from the last finished months (oldest first), sized to what the owner saves a month
    (`pace`) so each is reachable: stock for the next Eid by the month before stocking up starts (first,
    since it has a date), a cushion of about six months of saving, and a bulk stock order of about a
    year's. With nothing being saved yet, the amounts come from the shop's costs alone. Worked out here,
    never by the model."""
    if not months:
        return []
    usual = {k: int(statistics.median(m[k] for m in months)) for k in ("costs", "household", "stock")}
    monthly_need = usual["costs"] + usual["household"]
    goals = []
    if stock_up and usual["stock"] > 0:
        festival, starts = stock_up
        by = previous_month(month_start(starts))
        have = months_between(this_month, by)
        extra = max(max(m["stock"] for m in months) - usual["stock"], usual["stock"] // 4)
        amount = round_down_goal(min(extra, pace * have)) if pace > 0 else round_up_goal(extra)
        if amount > 0 and have > 0:
            how = (f"{have} month{'s' if have != 1 else ''} of saving at {fmt_taka(pace)} a month" if pace > 0
                   else "the extra stock a busy month needs")
            goals.append(GoalDraft(
                kind="eid_stock", name=f"{festival} stock", target_paisa=amount, by_month=by,
                why=(f"Part of the stock for {festival} paid from your own savings instead of supplier credit or a "
                     f"loan: {how}, ready by the end of {by:%B %Y}, before stocking up starts around "
                     f"{starts.day} {starts:%b}.")))
    if monthly_need > 0:
        cushion = round_up_goal(min(monthly_need, pace * 6) if pace > 0 else monthly_need)
        weeks = max(1, round(cushion / (monthly_need / 4.33)))
        span = (f"about {weeks} week{'s' if weeks != 1 else ''}" if cushion < monthly_need else "one month")
        goals.append(GoalDraft(
            kind="cushion", name="Emergency cushion", target_paisa=cushion,
            why=(f"{span[0].upper() + span[1:]} of the shop's running costs and your household money "
                 f"({fmt_taka(monthly_need)} a month), so a bad week or an illness never forces a loan"
                 + (", about six months of saving." if pace > 0 and cushion < monthly_need else "."))))
    if usual["stock"] > 0:
        bulk = round_up_goal(min(usual["stock"] // 2, pace * 12) if pace > 0 else usual["stock"] // 2)
        goals.append(GoalDraft(
            kind="grow_shop", name="Bulk stock order", target_paisa=bulk,
            why=("Your own money for a bigger stock order bought at once, which usually comes at a better price "
                 "from the supplier, or for a fridge or shelf that lets you sell more"
                 + (": about a year of saving." if pace > 0 else "."))))
    return goals


def months_between(start: date, end: date) -> int:
    """Whole months from `start`'s month to `end`'s month, counting both (Oct to Dec is 3)."""
    return (end.year - start.year) * 12 + end.month - start.month + 1


def fill_goals(goals: list[tuple[str, int, date | None]], *, saved_total: int, pace: int,
               this_month: date) -> list[dict]:
    """Share `saved_total` across goals in order (the first fills first). `goals`: (name, target, by_month).
    `pace`: what is being saved a month now. Per goal: saved, remaining, pct, months_left, on_track, text."""
    pool, out = max(0, saved_total), []
    queued = 0  # what the goals before this one still need: they fill first
    for name, target, by_month in goals:
        saved = min(target, pool)
        pool -= saved
        remaining = target - saved
        pct = round(saved * 100 / target) if target > 0 else 100
        months_left = None if remaining == 0 else (-(-(queued + remaining) // pace) if pace > 0 else None)
        on_track = None
        if by_month is not None and remaining > 0:
            have = months_between(this_month, by_month)
            on_track = months_left is not None and have > 0 and months_left <= have
        when = f" by {by_month:%b %Y}" if by_month else ""
        if remaining == 0:
            text = f"{name}: reached, {fmt_taka(target)} saved."
        elif months_left is None:
            text = (f"{name}: {fmt_taka(saved)} of {fmt_taka(target)}{when}. Nothing is being saved this month, "
                    "so no date yet.")
        else:
            pace_text = f"about {months_left} month{'s' if months_left != 1 else ''} at {fmt_taka(pace)} a month"
            late = "" if on_track in (None, True) else " That is later than you wanted: save a bigger share or move the date."
            text = f"{name}: {fmt_taka(saved)} of {fmt_taka(target)}{when}, {pace_text}.{late}"
        queued += remaining
        out.append({"saved": saved, "remaining": remaining, "pct": pct, "months_left": months_left,
                    "on_track": on_track, "reached": remaining == 0, "text": text})
    return out
