"""Which planner answers a turn: the offline rules or the model.

The rules (the mock planner) cost nothing and answer instantly, but they only understand plain phrases
and are gullible about forwarded content. So they take a turn only when ALL of this holds:

- the owner wrote it, with nothing forwarded or pasted alongside (no SMS, invoices or other people);
- it is short and plain: no negation, correction, condition, comparison or "why", one amount at most,
  no Bangla script (the rules read Banglish, not Bangla), and nothing that points back at earlier turns;
- the last reply did not ask the owner a question (then this message is an answer the rules can't place);
- the rules plan exactly one call, and it is one of the reads, a single ledger entry or a new safety buffer
  below (the buffer only changes when the forecast warns; the guard still checks the amount is the owner's). Payments,
  reminders, loans and credit always go to the model.

Anything else goes to the model. A wrong "model" costs a little money; a wrong "rules" gives the owner
a worse answer, so every doubt goes to the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.agent.llm.mock_provider import amounts_in, plan_owner_text
from app.agent.llm.types import HistoryMessage, TurnBrief
from app.core.money import normalize_digits

RULE_TOOLS = frozenset({
    "get_balances", "get_today_summary", "list_debtors", "forecast_cash", "list_recent_entries",
    "list_trusted_payees", "summarize_activity", "list_reminders", "add_ledger_entry", "list_bills",
    "get_safety_buffer", "set_safety_buffer", "record_customer_payment", "get_limits", "list_blocked_numbers",
    "get_savings", "set_savings_share", "get_growth_plan", "set_savings_goal", "add_starter_goals",
    "set_reinvest_share",
})
MAX_WORDS = 16
BANGLA = re.compile(r"[ঀ-৲৴-৿]")  # the Bengali block, except the taka sign ৳ (U+09F3)
# Words that need understanding, not pattern matching.
NUANCE = re.compile(
    r"\b(?:not|no|never|na|nah|don'?t|didn'?t|doesn'?t|isn'?t|wasn'?t|can'?t|won'?t|instead|actually|wait|but|"
    r"if|unless|should|could|would|why|how come|which|compare|better|best|worse|advice|advise|suggest|explain|"
    r"plan|keno|ki korbo|what about|how about|also|again|same|that|those|it|him|her|them|they|"
    r"each|per|half|minus|plus|discount|refund|returned?|cancel|undo|delete|remove|wrong|mistake|correct|fix|"
    r"change|edit|update)\b",
    re.I,
)


@dataclass(frozen=True)
class Route:
    planner: str  # "rules" | "model"
    reason: str


def choose(brief: TurnBrief, history: list[HistoryMessage]) -> Route:
    if brief.sender != "owner":
        return Route("model", "a message from someone other than the owner")
    if any(s.kind != "owner_text" for s in brief.segments):
        return Route("model", "forwarded content needs the model")
    text = normalize_digits(brief.owner_text).strip()
    if not text:
        return Route("model", "nothing typed")
    if BANGLA.search(text):
        return Route("model", "Bangla script")
    if len(text.split()) > MAX_WORDS:
        return Route("model", "a long message")
    if NUANCE.search(text):
        return Route("model", "needs understanding (negation, condition, comparison or a follow-up)")
    if len(amounts_in(text)) > 1:
        return Route("model", "more than one amount")
    if history and history[-1].role == "assistant" and history[-1].content.rstrip().endswith("?"):
        return Route("model", "answers the last reply's question")
    calls = plan_owner_text(text, brief.today)
    if len(calls) != 1:
        return Route("model", "not a phrase the offline rules know" if not calls else "more than one action")
    if calls[0].name not in RULE_TOOLS:
        return Route("model", f"{calls[0].name.replace('_', ' ')} is left to the model")
    return Route("rules", f"a plain {calls[0].name.replace('_', ' ')} request")
