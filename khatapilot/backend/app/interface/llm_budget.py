"""Each shop's monthly AI budget: what the model cost this calendar month, and whether it may still be used.

The provider reports every call as it returns; its tokens are priced at list prices and added to the
shop's month (`infra/llm_meter.py`). When the month's spend reaches `LLM_MONTHLY_BUDGET_USD`, every
turn is answered by the offline planner until the month ends (free, but it only knows plain phrases),
the chat marks those replies "budget reached", and the owner is told once at 80% and once at 100%.
The turn that crosses the line finishes on the model: the cap is checked before a turn starts.

With `LLM_MODE=mock` there is no model to pay for: everything is counted as offline and nothing is capped.
"""

from __future__ import annotations

from datetime import date

from app.agent.llm.anthropic_provider import estimate_cost
from app.core.clock import Clock, local_date
from app.core.config import Settings
from app.guard.recorder import Recorder
from app.infra.audit_log import AuditLog
from app.infra.db import Database
from app.infra.job_state import mark_once
from app.infra.llm_meter import MonthUsage, add_call, add_turn, month_usage

WARN_AT = 0.8


def _next_month(day: date) -> date:
    return date(day.year + (day.month == 12), day.month % 12 + 1, 1)


class LlmBudget:
    def __init__(self, *, settings: Settings, clock: Clock, db: Database, audit: AuditLog, model: str) -> None:
        self.settings = settings
        self.clock = clock
        self.db = db
        self.recorder = Recorder(audit)
        self.model = model
        self.offline_only = settings.llm_mode == "mock"

    @property
    def cap_usd(self) -> float:
        return max(0.0, float(self.settings.llm_monthly_budget_usd))

    def _today(self) -> date:
        return local_date(self.clock.now())

    def month(self) -> str:
        return f"{self._today():%Y-%m}"

    def usage(self) -> MonthUsage:
        with self.db.session() as session:
            return month_usage(session, self.settings.shop_id, self.month())

    # ---------------------------------------------------------------- recording

    def record_call(self, model: str, usage: dict[str, int]) -> None:
        """The provider's listener: one call to the model."""
        cost = estimate_cost(model, usage)
        if cost is None:
            cost = estimate_cost(self.model, usage)  # a dated or fallback id: price it as the configured model
        month = self.month()
        with self.db.session() as session:
            add_call(session, self.settings.shop_id, month, usage, cost)
            spent = month_usage(session, self.settings.shop_id, month).cost_usd
            self._maybe_alert(session, month, spent)

    def record_turn(self, route: str) -> None:
        with self.db.session() as session:
            add_turn(session, self.settings.shop_id, self.month(), "rules" if self.offline_only else route)

    def _maybe_alert(self, session, month: str, spent: float) -> None:
        cap = self.cap_usd
        if cap <= 0 or self.offline_only:
            return
        label = f"{self._today():%B}"
        resets = f"{_next_month(self._today()):%d %b}".lstrip("0")
        if spent >= cap and mark_once(session, f"llm-budget-100-{month}"):
            self.recorder.alert(
                request_id=f"llm-budget-{month}", channel="system",
                summary=f"AI budget used up for {label}",
                reason=(f"{self._money(spent)} of the {self._money(cap)} monthly AI budget is spent. Until {resets} "
                        "the offline planner answers every message for free: plain requests (sales, balances, who "
                        "owes, bills, savings) work as usual; anything it cannot read, it says so. Raise "
                        "LLM_MONTHLY_BUDGET_USD in .env to lift the cap."))
        elif spent >= cap * WARN_AT and mark_once(session, f"llm-budget-80-{month}"):
            self.recorder.alert(
                request_id=f"llm-budget-{month}", channel="system",
                summary=f"AI budget {spent / cap:.0%} used for {label}",
                reason=(f"{self._money(spent)} of the {self._money(cap)} monthly AI budget is spent. At "
                        f"{self._money(cap)} the offline planner takes over until {resets}, so plain requests "
                        "still work."))

    # ---------------------------------------------------------------- deciding and showing

    def capped(self) -> str | None:
        """Why the model may not be used now, or None."""
        cap = self.cap_usd
        if self.offline_only or cap <= 0:
            return None
        spent = self.usage().cost_usd
        if spent < cap:
            return None
        resets = _next_month(self._today())
        return (f"this month's AI budget ({self._money(cap)}) is used up; the offline planner answers until "
                f"{resets:%d %b}".replace(" 0", " "))

    def _money(self, usd: float) -> str:
        return f"US${usd:,.2f} (about ৳{usd * self.settings.usd_to_bdt:,.0f})"

    def status(self) -> dict:
        today = self._today()
        u = self.usage()
        turns = u.turns or {}
        free = turns.get("rules", 0) + turns.get("offline", 0) + turns.get("capped", 0)
        cap = self.cap_usd
        rate = self.settings.usd_to_bdt
        used = (u.cost_usd / cap) if cap > 0 else None
        capped = self.capped() is not None
        month = f"{today:%B}"
        if self.offline_only and u.total_turns == 0:
            text = f"Offline mode: no messages yet in {month}, and no AI cost."
        elif self.offline_only:
            text = (f"Offline mode: {'all ' if u.total_turns > 1 else ''}{u.total_turns} message"
                    f"{'s' if u.total_turns != 1 else ''} in {month} {'were' if u.total_turns != 1 else 'was'} "
                    "answered by the built-in planner, at no AI cost.")
        elif capped:
            text = (f"{month}'s AI budget is used up ({self._money(u.cost_usd)}). Until "
                    f"{_next_month(today):%d %b} the offline planner answers for free; it handles plain requests.")
        elif cap > 0:
            text = (f"{self._money(u.cost_usd)} of your {self._money(cap)} AI budget for {month}. "
                    f"{turns.get('model', 0)} message{'s' if turns.get('model', 0) != 1 else ''} used the model; "
                    f"{free} {'were' if free != 1 else 'was'} answered free offline.")
        else:
            text = f"{self._money(u.cost_usd)} of AI use in {month} (no monthly cap is set)."
        return {
            "month": u.month, "month_label": f"{today:%B %Y}", "mode": self.settings.llm_mode, "model": self.model,
            "offline_only": self.offline_only, "budget_usd": cap or None,
            "budget_bdt": round(cap * rate) if cap else None, "spent_usd": round(u.cost_usd, 4),
            "spent_bdt": round(u.cost_usd * rate), "used_pct": round(used * 100, 1) if used is not None else None,
            "capped": capped, "warn": used is not None and used >= WARN_AT, "resets_on": _next_month(today).isoformat(),
            "calls": u.calls, "unpriced_calls": u.unpriced_calls,
            "tokens": {"input": u.input_tokens, "output": u.output_tokens, "cache_read": u.cache_read_tokens,
                       "cache_write": u.cache_write_tokens},
            "turns": turns, "free_turns": free, "text": text,
        }
