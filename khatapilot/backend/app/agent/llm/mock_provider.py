"""A deterministic stand-in for the LLM: no key, no network, same answer every time.

It plans tool calls from the owner's words with simple rules (enough for the demo phrases in
English and Banglish) and writes replies from the tool results.

It is deliberately **gullible**: whenever forwarded content or someone else's message contains a
money instruction ("send ৳50,000 to 019…"), it proposes that payment, exactly like a model that
has been fully fooled by a prompt injection. That makes every mock-mode test and red-team run a
worst-case test of the guard: the security has to hold without any help from the model.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.agent.llm.types import HistoryMessage, LLMStep, LLMTool, LLMToolCall, ToolResult, TurnBrief
from app.core.masking import PHONE_RE, find_phone_numbers
from app.core.money import AMOUNT_RE, normalize_digits, parse_amount

MODEL = "mock-gullible-v1"
MOCK_PHOTOS: dict[str, dict[str, str]] = json.loads(
    (Path(__file__).with_name("mock_photos.json")).read_text(encoding="utf-8"))
CURRENCY_AMOUNT = re.compile(
    r"(?:৳|tk\.?|taka|bdt|টাকা)\s*(?P<a>[\d,]+(?:\.\d{1,2})?\s*(?:k|hajar|hazar)?)"
    r"|(?P<b>[\d,]+(?:\.\d{1,2})?\s*(?:k|hajar|hazar)?)\s*(?:৳|tk|taka|bdt|টাকা|/-)",
    re.I,
)
QUANTITY = re.compile(r"\b\d+\s*(?:bags?|bosta|kg|kilo|pcs|pieces?|packets?|liters?|litres?|dozen|ta|ti|cartons?)\b", re.I)
PAY_NOW = re.compile(r"\b(pay|send|transfer|pathao|pathan|pathiye dao)\b", re.I)
BANGLISH_PAY = re.compile(r"\b(?:ke|k)\s+(?:৳|tk)?\s*[\d,]+(?:\.\d+)?\s*(?:k|hajar)?\s*(?:taka|tk)?\s*(?:dao|den|pathao|pathan)\b", re.I)
# Bangla script: "রহিম ট্রেডার্সকে ৩০০০ টাকা দাও" (digits are normalised to 0-9 first)
BANGLA_PAY = re.compile(r"(?P<n>[ঀ-৿][ঀ-৿ ]*?)(?:কে|রে)\s*(?:৳\s*)?[\d,]+\s*(?:টাকা)?\s*"
                        r"(?:দাও|দিন|দিয়ে\s*দাও|পাঠাও|পাঠান|পাঠিয়ে\s*দাও)")
WEEKDAYS = {"monday": 0, "mon": 0, "sombar": 0, "tuesday": 1, "tue": 1, "mongolbar": 1, "wednesday": 2, "wed": 2,
            "budhbar": 2, "thursday": 3, "thu": 3, "brihospotibar": 3, "friday": 4, "fri": 4, "shukrobar": 4,
            "saturday": 5, "sat": 5, "shonibar": 5, "sunday": 6, "sun": 6, "robibar": 6}
NAME = r"[A-Za-z][A-Za-z&.'\-]*(?:\s+[A-Za-z][A-Za-z&.'\-]*){0,3}"


# ---------------------------------------------------------------- small parsers

def amounts_in(text: str) -> list[Decimal]:
    clean = QUANTITY.sub(" ", PHONE_RE.sub(" ", normalize_digits(text)))
    anchored = [parse_amount(m.group("a") or m.group("b")) for m in CURRENCY_AMOUNT.finditer(clean)]
    found = [a for a in anchored if a] or [parse_amount(m.group(0)) for m in AMOUNT_RE.finditer(clean)]
    return [Decimal(a) / 100 for a in found if a and a >= 100]


MONTH_NUMBERS = {name: i for i, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}


def _calendar_date(lowered: str, today: date) -> date | None:
    """'20 Oct', 'Oct 20' or '20/10' (the next such date on or after today)."""
    day = month = None
    if m := re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", lowered):
        day, month = int(m.group(1)), MONTH_NUMBERS[m.group(2)]
    elif m := re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{1,2})\b", lowered):
        day, month = int(m.group(2)), MONTH_NUMBERS[m.group(1)]
    elif m := re.search(r"\b(\d{1,2})/(\d{1,2})\b", lowered):
        day, month = int(m.group(1)), int(m.group(2))
    if day is None or month is None or not 1 <= month <= 12:
        return None
    try:
        found = date(today.year, month, day)
        return found if found >= today else date(today.year + 1, month, day)
    except ValueError:
        return None


def day_in(text: str, today: date) -> date | None:
    lowered = text.lower()
    if re.search(r"\b(tomorrow|kal|kalke|agamikal)\b", lowered):
        return today + timedelta(days=1)
    if (found := _calendar_date(lowered, today)) is not None:
        return found if found != today else None
    for word, weekday in WEEKDAYS.items():
        if re.search(rf"\b{word}\b", lowered):
            target = today + timedelta(days=(weekday - today.weekday()) % 7)
            return target if target != today else None
    return None


def method_in(text: str, default: str | None = None) -> str | None:
    lowered = text.lower()
    for method in ("bkash", "nagad", "bank"):
        if method in lowered:
            return method
    return default


LEADING_FILLER = {"to", "the", "my", "from", "for", "ke", "k", "a", "an"}
NAME_ENDS = {"on", "baki", "bakite", "credit", "udhar", "due", "today", "tomorrow", "by", "with", "at", "via", "cash",
             "bkash", "nagad", "bank", "and", "then", "ke", "k", "taka", "tk"}


def clip_name(raw: str | None) -> str | None:
    """Trim filler in front and stop at the first word that cannot be part of a name ("on baki")."""
    if not raw:
        return None
    words = raw.split()
    while words and words[0].lower() in LEADING_FILLER:
        words.pop(0)
    name = []
    for word in words:
        if word.lower() in NAME_ENDS:
            break
        name.append(word)
    return " ".join(name) or None


def call(name: str, **arguments: Any) -> LLMToolCall:
    arguments = {k: v for k, v in arguments.items() if v is not None}
    digest = hashlib.sha1(f"{name}:{json.dumps(arguments, sort_keys=True, default=str)}".encode()).hexdigest()[:10]
    return LLMToolCall(id=f"mock_{name}_{digest}", name=name, arguments=arguments)


def money_instructions(text: str) -> list[tuple[str, Decimal]]:
    """Every "send <amount> to <number>" style instruction, wherever it appears. A gullible model obeys these."""
    found = []
    for sentence in re.split(r"[.\n;!]", normalize_digits(text)):
        if not re.search(r"\b(send|transfer|pay|pathao|pathan|dao|bkash|nagad)\b", sentence, re.I):
            continue
        numbers = find_phone_numbers(sentence)
        amounts = amounts_in(sentence)
        if numbers and amounts:
            found.append((numbers[0], max(amounts)))
    return found


# ---------------------------------------------------------------- planning

def plan_owner_text(text: str, today: date) -> list[LLMToolCall]:
    t = normalize_digits(text).strip()
    low = t.lower()
    amounts = amounts_in(t)
    numbers = find_phone_numbers(t)
    calls: list[LLMToolCall] = []

    # Credit profile and loans (checked first: "a loan to pay Meghna" is a loan question, not a payment)
    if re.search(r"\b(credit (?:score|profile|rating)|score koto|creditworth\w*)\b", low) and \
            not re.search(r"\b(loans?|rin|apply)\b", low):
        return [call("build_credit_profile")]
    if re.search(r"\b(loans?|rin|borrow)\b", low):
        purpose = ("eid_stock" if "eid" in low else "cover_shortfall" if re.search(r"\b(short|shortfall|cover)\b", low)
                   else "stock" if re.search(r"\b(stock|maal|mal)\b", low) else "working_capital")
        amount = {"amount_taka": str(max(amounts))} if amounts else {}
        if re.search(r"\b(apply|aply|application|abedon)\b", low):
            product = next((pid for pattern, pid in (
                (r"\b(microfinance|mfi|ngo)\b", "mfi-working-capital"), (r"\bbank\b", "bank-sme"),
                (r"\bsupplier\b", "supplier-credit"), (r"\b(nano|wallet|bkash)\b", "wallet-nano"))
                if re.search(pattern, low)), None)
            if product:
                return [call("prefill_application", product_id=product, purpose=purpose, **amount)]
        return [call("build_credit_profile"), call("match_loans", purpose=purpose, **amount)]

    # Pay someone (an instruction for now or a later day)
    if (PAY_NOW.search(low) and not re.search(r"\bpaid\b", low)) or BANGLISH_PAY.search(low) or BANGLA_PAY.search(t):
        for clause in re.split(r"\s+(?:and|then|ar|আর|এবং)\s+", t, flags=re.I):
            clause_amounts = amounts_in(clause)
            if not clause_amounts:
                continue
            clause_numbers = find_phone_numbers(clause)
            name = None
            if m := re.search(rf"\b(?:pay|send|transfer)\s+(?:to\s+)?(?P<n>{NAME}?)\s+(?:৳|tk|taka)?\s*\d", clause, re.I):
                name = m.group("n")
            elif m := re.search(rf"(?P<n>{NAME}?)\s+(?:ke|k)\s+(?:৳|tk)?\s*\d", clause, re.I):
                name = m.group("n")
            elif m := re.search(rf"^\s*(?P<n>{NAME}?)\s+(?:৳|tk|taka)?\s*\d", clause, re.I):
                name = m.group("n")
            elif m := BANGLA_PAY.search(clause):
                name = m.group("n").strip()
            name = clip_name(name)
            if name and name.lower() in {"pay", "send", "transfer"}:
                name = None
            if not name and not clause_numbers:
                continue
            calls.append(call("propose_payment", payee_name=name, payee_number=clause_numbers[0] if clause_numbers else None,
                              amount_taka=str(clause_amounts[0]), method=method_in(clause),
                              execute_on=str(d) if (d := day_in(clause, today)) else None))
        return calls

    amount = str(amounts[0]) if amounts else None
    item = None
    if m := re.search(r"\b\d+\s*(?:bags?|bosta|kg|kilo|packets?|liters?|litres?|pcs|dozen)\s+(?:of\s+)?(?P<item>[a-z]+)", low):
        item = m.group(0)
    elif m := re.search(r"\b(rice|chal|dal|oil|tel|sugar|chini|soap|biscuits?|milk|eggs?|atta|flour|onions?)\b", low):
        item = m.group(0)

    # Record a sale
    if amount and re.search(r"\b(sold|sell|sale|bikri|bechi|becha|beche|bechlam)\b", low):
        on_credit = re.search(r"\b(baki|bakite|credit|udhar|due)\b", low)
        if on_credit:
            who = None
            if m := re.search(rf"\bto\s+(?P<n>{NAME})", t):
                who = m.group("n")
            elif m := re.search(rf"(?P<n>{NAME})\s+(?:ke|k)\s+baki", t, re.I):
                who = m.group("n")
            return [call("add_ledger_entry", kind="baki_sale", amount_taka=amount, method="credit",
                         counterparty_name=clip_name(who), item=item)]
        return [call("add_ledger_entry", kind="sale", amount_taka=amount, method=method_in(low, "cash"), item=item)]

    # Record a purchase
    if amount and re.search(r"\b(bought|buy|purchased|kinlam|kinechi|kena|kinsi)\b", low):
        supplier = m.group("n") if (m := re.search(rf"\bfrom\s+(?P<n>{NAME})", t)) else None
        credit = bool(re.search(r"\b(baki|credit|udhar|due)\b", low))
        return [call("add_ledger_entry", kind="purchase", amount_taka=amount,
                     method="credit" if credit else method_in(low, "cash"), counterparty_name=clip_name(supplier),
                     item=item)]

    # A customer paid back baki
    if amount and (m := re.search(rf"(?P<n>{NAME})\s+(?:paid|dilo|dise|dyse|ferot dilo)\b", t, re.I)) and not \
            re.search(r"^\s*(?:i|ami)\s", low):
        return [call("add_ledger_entry", kind="baki_repayment", amount_taka=amount, method=method_in(low, "cash"),
                     counterparty_name=clip_name(m.group("n")))]

    # Record money already paid out (a bill, a supplier)
    if amount and re.search(r"\b(paid|dilam|diyechi|pay korsi|pay korlam|khoroch)\b", low):
        if re.search(r"\b(rent|bhara|electricity|current|bill|salary|beton|van|transport|tea)\b", low):
            return [call("add_ledger_entry", kind="expense", amount_taka=amount, method=method_in(low, "cash"),
                         item=re.search(r"\b(rent|bhara|electricity|current|bill|salary|beton|van|transport|tea)\b",
                                        low).group(0))]
        who = m.group("n") if (m := re.search(rf"\bpaid\s+(?:to\s+)?(?P<n>{NAME}?)\s+(?:৳|tk|taka)?\s*\d", t, re.I)) else None
        return [call("add_ledger_entry", kind="supplier_payment", amount_taka=amount, method=method_in(low, "cash"),
                     counterparty_name=clip_name(who))]

    # Baki reminders (before the debtor question: "remind everyone who owes me" is a request, not a question)
    if re.search(r"\b(remind|reminders?|tagada|mone koriye)\b", low):
        if re.search(r"\b(who paid|paid since|did they pay|kara dise|ke dise)\b", low):
            return [call("list_reminders")]
        days = 14
        if m := re.search(r"(?:over|more than|beshi|at least)\s+(\d+)\s*(day|days|week|weeks|din|shoptah)", low):
            days = int(m.group(1)) * (7 if m.group(2).startswith(("week", "shoptah")) else 1)
        language = "banglish" if re.search(r"\b(banglish|bangla|tagada)\b", low) else "english"
        return [call("send_reminders", min_days=days, language=language)]

    # Questions
    if re.search(r"\b(owe|owes|owed|debtor|debtors|baki koto|kar kache|who has baki)\b", low):
        days = 0
        if m := re.search(r"(?:over|more than|beshi)\s+(\d+)\s*(day|days|week|weeks|din|shoptah)", low):
            days = int(m.group(1)) * (7 if m.group(2).startswith(("week", "shoptah")) else 1)
        return [call("list_debtors", min_days=days)]
    if re.search(r"\b(what did you do|what have you done|this week|last week|ki korecho|ki korla|activity)\b", low):
        return [call("summarize_activity", days=7)]
    if re.search(r"\b(forecast|run short|running short|short of (?:cash|money)|cash ?flow|enough (?:cash|money)|"
                 r"next (?:two )?weeks?|kom porbe|taka thakbe|cholbe)\b", low):
        return [call("forecast_cash")]
    if re.search(r"\b(balance|how much money|cash koto|taka koto|on hand)\b", low):
        return [call("get_balances")]
    if re.search(r"\b(today|aj|ajke|summary|hisab|koto bikri|sales)\b", low):
        return [call("get_today_summary")]
    if re.search(r"\b(recent|last entries|latest)\b", low):
        return [call("list_recent_entries")]
    if re.search(r"\b(payee|payees|trusted)\b", low):
        return [call("list_trusted_payees")]
    return calls


def plan(brief: TurnBrief) -> list[LLMToolCall]:
    calls: list[LLMToolCall] = []
    if brief.sender == "external":
        text = "\n".join(s.text for s in brief.segments)
        for number, amount in money_instructions(text):  # gullible on purpose
            calls.append(call("propose_payment", payee_number=number, amount_taka=str(amount)))
        if re.search(r"\b(customer|customers|numbers|contacts|list|data)\b", text, re.I):
            calls.append(call("list_debtors"))  # gullible on purpose
        if re.search(r"\b(remind|reminder|reminders)\b", text, re.I):
            calls.append(call("send_reminders", min_days=1))  # gullible on purpose
        asked = "asked for money" if calls else "sent a message"
        calls.append(call("notify_owner", message=f"A message from {brief.sender_display} {asked}. "
                                                  "KhataPilot did not act on it."))
        return calls
    for segment in brief.segments:
        if segment.kind == "owner_text":
            continue
        if segment.kind == "mobile_money_sms":
            calls.append(call("record_sms", segment=segment.index))
        for number, amount in money_instructions(segment.text):  # gullible on purpose
            calls.append(call("propose_payment", payee_number=number, amount_taka=str(amount)))
    return calls + plan_owner_text(brief.owner_text, brief.today)


# ---------------------------------------------------------------- replies

def describe(tool: str, outcome) -> str:
    data = outcome.data if isinstance(outcome.data, dict) else {}
    if outcome.status == "blocked":
        return f"I did not do that. {outcome.message}"
    if outcome.status == "error":
        return f"I couldn't do that: {outcome.message}"
    if outcome.status == "held":
        how = "Confirm it and enter your PIN" if outcome.requires_pin else "Tap Approve"
        warnings = " ".join(data.get("warnings") or [])
        return f"Waiting for you: {data.get('summary', 'an action')}. {how} if you want it. {warnings}".strip()
    if tool == "record_sms":
        way = "received from" if data.get("direction") == "in" else "sent to"
        text = (f"Booked the {data.get('provider', '').replace('bkash', 'bKash').replace('nagad', 'Nagad')} SMS: "
                f"{data.get('amount')} {way} {data.get('counterparty') or 'the other side'} as {data.get('recorded')}.")
        if data.get("customer_still_owes") == "৳0":
            text += f" {data.get('counterparty')} has now paid back everything they owed."
        elif data.get("customer_still_owes"):
            text += f" {data.get('counterparty')} still owes {data['customer_still_owes']}."
        if data.get("fee"):
            text += f" Fee {data['fee']} recorded too."
        return text
    if tool == "add_ledger_entry":
        text = f"Recorded {data.get('recorded')} {data.get('amount')} ({data.get('method')}, {data.get('category')})."
        if data.get("customer_now_owes"):
            text += f" They now owe {data['customer_now_owes']}."
        return text
    if tool == "propose_payment":
        return outcome.message
    if tool == "get_today_summary":
        return (f"Today so far: sales {data['sales_today']}, baki given {data['baki_given_today']}, baki collected "
                f"{data['baki_collected_today']}; money in {data['money_in_today']}, out {data['money_out_today']}. "
                f"On hand: {data['total_on_hand']}.")
    if tool == "get_balances":
        b = data.get("balances", {})
        return (f"On hand: cash {b.get('cash')}, bKash {b.get('bkash')}, Nagad {b.get('nagad')}, bank {b.get('bank')} "
                f"(total {data.get('total')}).")
    if tool == "list_debtors":
        people = data.get("customers", [])
        if not people:
            return "Nobody owes you money right now."
        top = "; ".join(f"{p['name']} {p['owes']} ({p['days']} days)" for p in people[:5])
        return f"{data['count']} customers owe you {data['total_owed']}: {top}."
    if tool in ("build_credit_profile", "match_loans") and data.get("needs_consent"):
        return data["message"]
    if tool == "build_credit_profile":
        text = data.get("summary", "")
        if data.get("helps"):
            text += " Helping you: " + " ".join(data["helps"][:2])
        if data.get("hurts"):
            text += " Holding you back: " + " ".join(data["hurts"][:1])
        return text
    if tool == "match_loans":
        return f"{data.get('recommendation', '')} (Sample products, for illustration.)"
    if tool == "summarize_activity":
        parts = [f"In the last {data.get('days', 7)} days I recorded {data.get('entries_recorded', 0)} entries"]
        if data.get("paid"):
            parts.append(f"made {len(data['paid'])} payment(s) ({'; '.join(data['paid'][:3])})")
        if data.get("scheduled"):
            parts.append(f"scheduled {len(data['scheduled'])}")
        if data.get("loan_applications_sent"):
            parts.append(f"sent {len(data['loan_applications_sent'])} loan application(s)")
        if data.get("reminders_sent"):
            parts.append(f"sent {len(data['reminders_sent'])} baki reminder(s)")
        text = ", ".join(parts) + "."
        if data.get("blocked"):
            examples = "; ".join(f"{b['what']} ({b['why']})" for b in data.get("blocked_examples", [])[:2])
            text += f" I blocked {data['blocked']} attempt(s): {examples}."
        else:
            text += " Nothing had to be blocked."
        if data.get("waiting_for_you"):
            text += f" {data['waiting_for_you']} thing(s) are waiting for your approval."
        return text
    if tool == "forecast_cash":
        text = data.get("headline", "")
        fixes = [s["what"] for s in data.get("suggestions", []) if s.get("closes_gap")][:2]
        if fixes:
            text += " What would fix it: " + " Or: ".join(fixes)
        return text
    if tool == "list_recent_entries":
        lines = [f"{e['kind'].replace('_', ' ')} {e['amount']} ({e['method']})" for e in data.get("entries", [])[:5]]
        return "Latest entries: " + "; ".join(lines) + "."
    if tool == "list_trusted_payees":
        payees = outcome.data or []
        return "Trusted payees: " + ", ".join(f"{p['name']} ({p['number']}, {p['method']})" for p in payees) + "."
    if tool == "list_reminders":
        rows = data.get("reminders", [])
        if not rows:
            return "No baki reminders have been sent yet."
        who = "; ".join(f"{r['customer']}: {r['outcome'].replace('_', ' ')}, still owes {r['still_owes']}" for r in rows[:5])
        return (f"Since the reminders: {data.get('paid', 0)} paid in full, {data.get('part_paid', 0)} in part, "
                f"{data.get('waiting', 0)} not yet. {who}.")
    if tool == "notify_owner":
        return "I told the owner about this message and did nothing else."
    return outcome.message


def compose(brief: TurnBrief, calls: list[LLMToolCall], results: list[ToolResult]) -> str:
    if not calls:
        return ("I can record sales, purchases and bKash/Nagad SMS, show today's totals and balances, list who owes "
                "you, warn you before cash runs short, pay suppliers within your limits, and find you a loan. "
                "For example: "
                "\"sold 3 bags of rice ৳1,500 cash\".")
    by_id = {r.call_id: r.outcome for r in results}
    parts = list(dict.fromkeys(describe(c.name, by_id[c.id]) for c in calls if c.id in by_id))
    ledger_today = next((by_id[c.id].data.get("today") for c in reversed(calls)
                         if c.name in ("record_sms", "add_ledger_entry") and c.id in by_id
                         and by_id[c.id].status == "ok" and isinstance(by_id[c.id].data, dict)), None)
    if ledger_today:
        parts.append(f"Today: sales {ledger_today['sales_today']}, money in {ledger_today['money_in_today']}, "
                     f"out {ledger_today['money_out_today']}.")
    return " ".join(parts)


class MockSession:
    def __init__(self) -> None:
        self.brief: TurnBrief | None = None
        self.calls: list[LLMToolCall] = []
        self.results: list[ToolResult] = []
        self.planned = False

    def user(self, content: str, brief: TurnBrief) -> None:
        self.brief = brief

    def step(self) -> LLMStep:
        if not self.planned:
            self.planned = True
            self.calls = plan(self.brief)
            if self.calls:
                return LLMStep(text="", tool_calls=self.calls, stop_reason="tool_use", model=MODEL)
        return LLMStep(text=compose(self.brief, self.calls, self.results), tool_calls=[], stop_reason="end_turn",
                       model=MODEL)

    def tool_results(self, results: list[ToolResult]) -> None:
        self.results.extend(results)


class MockProvider:
    name = "mock"
    model = MODEL

    def session(self, *, system: str, tools: list[LLMTool], history: list[HistoryMessage]) -> MockSession:
        return MockSession()

    def extract(self, *, system: str, text: str, schema: dict[str, Any]) -> dict[str, Any] | None:
        """Invoice-like text: the biggest amount near "total" and the first line as the supplier."""
        body = normalize_digits(text)
        total = re.search(r"\btotal\b[^\d]{0,15}([\d,]+(?:\.\d{1,2})?)", body, re.I)
        if not total:
            return None
        number = re.search(r"\b(?:invoice|inv|bill)\s*(?:no\.?|#)?\s*([A-Za-z0-9-]{2,20})", body, re.I)
        first_line = body.strip().splitlines()[0][:80] if body.strip() else ""
        supplier = re.sub(r"(?i)\b(?:invoice|inv|bill)\b.*$", "", first_line).strip(" :#-") or "Unknown supplier"
        due = re.search(r"\bdue\b[^\d]{0,10}(\d{1,2}/\d{1,2}(?:/\d{2,4})?)", body, re.I)
        return {"supplier_name": supplier, "invoice_number": number.group(1) if number else "",
                "total_taka": float(total.group(1).replace(",", "")), "due_date": due.group(1) if due else ""}

    def extract_image(self, *, system: str, image: bytes, media_type: str,
                      schema: dict[str, Any]) -> dict[str, Any] | None:
        """The mock cannot see. It knows the demo's sample photos by their SHA-256 and returns the text
        printed on them (mock_photos.json); any other photo is unreadable."""
        text = MOCK_PHOTOS.get(hashlib.sha256(image).hexdigest(), {}).get("text")
        return {"readable": True, "text": text} if text else None
