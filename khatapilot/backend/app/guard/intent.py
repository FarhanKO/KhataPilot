"""Owner intents: what the owner actually asked to pay, read only from text the owner typed.

Extraction is deterministic (no LLM), so text hidden in a forwarded SMS or invoice can never create
an intent, and the model cannot invent one. Every value is grounded: the amount and the payee
literally appear in the owner's words. The guard then requires every proposed payment to match
an unused intent exactly (payee, amount, date); each intent authorizes at most one payment.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.actions import PaymentRequest
from app.core.clock import Clock
from app.core.ids import new_id
from app.core.masking import PHONE_RE, find_phone_numbers, mask_phone, mask_text
from app.core.money import AMOUNT_RE, fmt_taka, normalize_digits, parse_amount
from app.core.provenance import TurnContext
from app.guard.models import OwnerIntentRecord
from app.guard.policy_store import TrustedPayeeView, normalize_name

PAY_WORDS = (
    r"pay|send|transfer|remit|settle|clear|pathao|pathan|pathiye\s+dao|diye\s+dao|diye\s+den|dao|den|"
    r"porishodh\s+koro|shodh\s+koro|pay\s+koro|bkash\s+koro|nagad\s+koro|"
    r"পাঠাও|পাঠান|পাঠিয়ে\s*দাও|দিয়ে\s*দাও|দাও|দিন|পরিশোধ\s*কর(?:ো|ুন)?|শোধ\s*কর(?:ো|ুন)?"
)
PAY_RE = re.compile(rf"(?<![\wঀ-৿])(?:{PAY_WORDS})(?![\wঀ-৿])", re.IGNORECASE)
CURRENCY = r"(?:৳|tk\.?|taka|bdt|টাকা|/-)"
CURRENCY_AMOUNT = re.compile(
    rf"{CURRENCY}\s*(?P<a>[\d,]+(?:\.\d{{1,2}})?\s*(?:k|hajar|hazar|হাজার|lakh|lac|লাখ)?)"
    rf"|(?P<b>[\d,]+(?:\.\d{{1,2}})?\s*(?:k|hajar|hazar|হাজার|lakh|lac|লাখ)?)\s*{CURRENCY}",
    re.IGNORECASE,
)
QUANTITY = re.compile(
    r"\b\d+\s*(?:bags?|bosta|boshta|kg|kgs|kilo|kilos|pcs|pieces?|packets?|packs?|liters?|litres?|ltr|dozen|"
    r"ta|ti|khana|cartons?|boxes|box|sacks?|units?|items?|days?|hours?|hrs?|mins?|minutes?|weeks?|months?)\b",
    re.IGNORECASE,
)
MONTHS = {m: i for i, names in enumerate(
    [("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",), ("jun", "june"),
     ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
     ("dec", "december")], start=1) for m in names}
MONTH_NAMES = "|".join(sorted(MONTHS, key=len, reverse=True))
DATE_DMY = re.compile(r"\b(?P<d>\d{1,2})[/-](?P<m>\d{1,2})(?:[/-](?P<y>\d{2,4}))?\b")
DATE_D_MON = re.compile(rf"\b(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<mon>{MONTH_NAMES})\b", re.IGNORECASE)
DATE_MON_D = re.compile(rf"\b(?P<mon>{MONTH_NAMES})\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\b", re.IGNORECASE)
TIME_RE = re.compile(r"\b\d{1,2}[:.]\d{2}\s*(?:am|pm)?\b|\b\d{1,2}\s*(?:am|pm)\b", re.IGNORECASE)
WEEKDAYS = {
    0: r"monday|mon|sombar|shombar|সোমবার", 1: r"tuesday|tue|tues|mongolbar|মঙ্গলবার",
    2: r"wednesday|wed|budhbar|budhvar|বুধবার", 3: r"thursday|thu|thurs|brihospotibar|bishudbar|বৃহস্পতিবার",
    4: r"friday|fri|shukrobar|sukrobar|শুক্রবার", 5: r"saturday|sat|shonibar|sonibar|শনিবার",
    6: r"sunday|sun|robibar|rabibar|রবিবার",
}
TODAY_RE = re.compile(r"(?<![\wঀ-৿])(today|now|right\s+now|ajke|aaj|ekhon|ekhoni|আজ|আজকে|এখন|এখনই)(?![\wঀ-৿])", re.IGNORECASE)
TOMORROW_RE = re.compile(r"(?<![\wঀ-৿])(tomorrow|tmrw|kal|kalke|agamikal|আগামীকাল|কাল|কালকে)(?![\wঀ-৿])", re.IGNORECASE)
METHODS = {"bkash": r"bkash|bikash|বিকাশ", "nagad": r"nagad|নগদ", "bank": r"bank|ব্যাংক|ব্যাঙ্ক"}
# Sentences end at . ; ! ? or a newline, but never at the dot in "Tk. 500" or "8.5k". Inside a sentence
# that asks for a payment, "and"/"then" separate payments that share the verb ("pay A 4k and B 5k").
SENTENCE_SPLIT = re.compile(r"(?<!tk)\.(?=\s|$)|[;\n!?]+", re.IGNORECASE)
CLAUSE_SPLIT = re.compile(r"\s+(?:and\s+then|then|and|also|ar|আর|এবং)\s+", re.IGNORECASE)
BANGLA_CASE_SUFFIX = re.compile(r"([ঀ-৿]+?)(কেই|কে|রে|এর)(?=\s|$)")
NAME_STOP = (r"(?=\s+(?:৳|tk|taka|bdt|টাকা|\d|on|by|via|from|with|at|for|tomorrow|today|now|ke|k|kay|re|er|"
             r"bkash|nagad|bank)\b|\s*$|\s*\d)")
NAME_PATTERNS = (
    re.compile(rf"\b(?:pay|send|transfer|settle|clear)\s+(?:to\s+)?(?P<name>[a-z][a-z&.'\- ]{{1,40}}?){NAME_STOP}", re.IGNORECASE),
    re.compile(rf"\bto\s+(?P<name>[a-z][a-z&.'\- ]{{1,40}}?){NAME_STOP}", re.IGNORECASE),
    re.compile(r"(?P<name>[a-z][a-z&.'\- ]{1,40}?)\s+(?:ke|k|kay|re)\s+(?:৳|tk)?\s*\d", re.IGNORECASE),
    # "... and Meghna 5,000": a later clause of a payment sentence starts with the payee
    re.compile(rf"^\s*(?P<name>[a-z][a-z&.'\- ]{{1,40}}?){NAME_STOP}", re.IGNORECASE),
)
FILLER = {"the", "my", "our", "a", "an", "money", "taka", "tk", "bill", "due", "dues", "please", "pls", "plz", "him",
          "her", "them", "it", "this", "that", "amount", "supplier", "payment", "cash", "bkash", "nagad", "bank",
          "to", "for", "bhai", "vai", "apa", "sir"}


class OwnerIntent(BaseModel):
    model_config = ConfigDict(frozen=True)

    payee_name: str | None
    payee_number: str | None
    amounts: tuple[int, ...]
    execute_on: date | None
    method: str | None
    clause: str  # the owner's words, numbers masked

    def describe(self) -> str:
        who = self.payee_name or (mask_phone(self.payee_number) if self.payee_number else "someone")
        amount = " or ".join(fmt_taka(a) for a in self.amounts) or "an unstated amount"
        when = f" on {self.execute_on:%a %d %b}" if self.execute_on else ""
        return f"pay {who} {amount}{when}"


class IntentMatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    matched: bool
    index: int | None
    detail: str
    ambiguous: bool = False


# ---------------------------------------------------------------- extraction

def _next_weekday(today: date, weekday: int) -> date:
    return today + timedelta(days=(weekday - today.weekday()) % 7)


def _extract_date(clause: str, today: date) -> tuple[date | None, str]:
    """Return the date the owner named (if any) and the clause with that date text removed."""
    for pattern in (DATE_D_MON, DATE_MON_D):
        match = pattern.search(clause)
        if match:
            month = MONTHS[match.group("mon").lower()]
            day = int(match.group("d"))
            year = today.year + (1 if month < today.month - 6 else 0)
            try:
                return date(year, month, day), clause[:match.start()] + " " + clause[match.end():]
            except ValueError:
                pass
    match = DATE_DMY.search(clause)
    if match:
        day, month = int(match.group("d")), int(match.group("m"))
        year = int(match.group("y")) if match.group("y") else today.year
        year = year + 2000 if year < 100 else year
        try:
            return date(year, month, day), clause[:match.start()] + " " + clause[match.end():]
        except ValueError:
            pass
    if TOMORROW_RE.search(clause):
        return today + timedelta(days=1), clause
    for weekday, names in WEEKDAYS.items():
        if re.search(rf"(?<![\wঀ-৿])(?:{names})(?![\wঀ-৿])", clause, re.IGNORECASE):
            return _next_weekday(today, weekday), clause
    if TODAY_RE.search(clause):
        return today, clause
    return None, clause


def _extract_amounts(clause: str) -> tuple[int, ...]:
    text = PHONE_RE.sub(" ", clause)
    text = TIME_RE.sub(" ", text)
    text = QUANTITY.sub(" ", text)
    anchored = []
    for match in CURRENCY_AMOUNT.finditer(text):
        value = parse_amount(match.group("a") or match.group("b"))
        if value:
            anchored.append(value)
    if anchored:
        return tuple(dict.fromkeys(anchored))
    loose = []
    for match in AMOUNT_RE.finditer(text):
        digits = match.group("num").replace(",", "").split(".")[0]
        if len(digits) >= 10:
            continue
        value = parse_amount(match.group(0))
        if value and value >= 100:  # at least ৳1
            loose.append(value)
    return tuple(dict.fromkeys(loose))


def _extract_payee_name(clause: str, known_names: Iterable[str]) -> str | None:
    without_suffixes = BANGLA_CASE_SUFFIX.sub(r"\1", clause)
    norm_clause = f" {normalize_name(without_suffixes)} "
    hits = [name for name in known_names if normalize_name(name) and f" {normalize_name(name)} " in norm_clause]
    if hits:
        return max(hits, key=len)
    for pattern in NAME_PATTERNS:
        match = pattern.search(clause)
        if match:
            words = [w for w in re.split(r"\s+", match.group("name").strip()) if w]
            while words and (words[-1].lower() in FILLER or PAY_RE.fullmatch(words[-1])):
                words.pop()
            while words and (words[0].lower() in FILLER or PAY_RE.fullmatch(words[0])):
                words.pop(0)
            if words:
                return " ".join(words)[:64]
    return None


def extract_payment_intents(owner_text: str, *, today: date, known_names: Iterable[str] = ()) -> list[OwnerIntent]:
    known = list(known_names)
    intents: list[OwnerIntent] = []
    clauses = []
    for sentence in SENTENCE_SPLIT.split(normalize_digits(owner_text)):
        if sentence and PAY_RE.search(sentence):
            clauses += [c.strip() for c in CLAUSE_SPLIT.split(sentence) if c.strip()]
    for clause in clauses:
        execute_on, without_date = _extract_date(clause, today)
        amounts = _extract_amounts(without_date)
        numbers = find_phone_numbers(clause)
        name = _extract_payee_name(clause, known)
        if not amounts or not (numbers or name):
            continue
        method = next((m for m, pattern in METHODS.items() if re.search(pattern, clause, re.IGNORECASE)), None)
        intents.append(OwnerIntent(
            payee_name=name, payee_number=numbers[0] if numbers else None, amounts=amounts,
            execute_on=execute_on if execute_on and execute_on != today else None,
            method=method, clause=mask_text(clause)[:200],
        ))
    return intents


# ---------------------------------------------------------------- matching

def names_equivalent(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    x, y = normalize_name(a), normalize_name(b)
    if not x or not y:
        return False
    return x == y or (min(len(x), len(y)) >= 4 and (x.startswith(y) or y.startswith(x)))


def match_payment(request: PaymentRequest, intents: list[OwnerIntent], *, today: date,
                  payee_by_name: Callable[[str | None], TrustedPayeeView | None]) -> IntentMatch:
    if not intents:
        return IntentMatch(matched=False, index=None,
                           detail="You did not ask for this payment in your own words in this conversation.")
    wanted_day = request.execute_on or today
    best: tuple[int, int] | None = None
    for index, intent in enumerate(intents):
        if intent.payee_number:
            payee_ok = request.payee_number == intent.payee_number
        else:
            trusted = payee_by_name(intent.payee_name)
            same_trusted = bool(trusted and payee_by_name(request.payee_name) == trusted)
            payee_ok = names_equivalent(request.payee_name, intent.payee_name) or same_trusted or bool(
                trusted and request.payee_number and trusted.number == request.payee_number)
        amount_ok = request.amount_paisa in intent.amounts
        date_ok = (intent.execute_on or today) == wanted_day
        method_ok = intent.method is None or intent.method == request.method
        if payee_ok and amount_ok and date_ok and method_ok:
            return IntentMatch(matched=True, index=index, ambiguous=len(intent.amounts) > 1,
                               detail=f"Matches your words: “{intent.clause}”")
        score = payee_ok * 4 + amount_ok * 2 + date_ok
        if best is None or score > best[0]:
            best = (score, index)
    intent = intents[best[1]]
    asked = intent.describe()
    got = (f"pay {request.payee_name or mask_phone(request.payee_number or '')} {fmt_taka(request.amount_paisa)}"
           + (f" on {request.execute_on:%a %d %b}" if request.execute_on and request.execute_on != today else ""))
    return IntentMatch(matched=False, index=None,
                       detail=f"This does not match what you asked. You said: {asked}. This payment would {got}.")


# ---------------------------------------------------------------- storage

class IntentStore:
    """Intents live for a while per conversation, so "yes" on the next turn still refers to them."""

    def __init__(self, session: Session, clock: Clock, shop_id: str, ttl_minutes: int = 30) -> None:
        self.session = session
        self.clock = clock
        self.shop_id = shop_id
        self.ttl = timedelta(minutes=ttl_minutes)

    def record_turn(self, turn: TurnContext, intents: list[OwnerIntent]) -> None:
        exists = self.session.scalars(select(OwnerIntentRecord).where(
            OwnerIntentRecord.request_id == turn.request_id).limit(1)).first()
        if exists:
            return
        now = self.clock.now()
        for intent in intents:
            self.session.add(OwnerIntentRecord(
                id=new_id("intent"), shop_id=self.shop_id, conversation_id=turn.conversation_id,
                request_id=turn.request_id, intent_json=intent.model_dump_json(), created_at=now,
                expires_at=now + self.ttl,
            ))
        self.session.flush()

    def active(self, conversation_id: str) -> list[tuple[str, OwnerIntent]]:
        now: datetime = self.clock.now()
        rows = self.session.scalars(select(OwnerIntentRecord).where(
            OwnerIntentRecord.shop_id == self.shop_id, OwnerIntentRecord.conversation_id == conversation_id,
            OwnerIntentRecord.consumed_by.is_(None), OwnerIntentRecord.expires_at > now,
        ).order_by(OwnerIntentRecord.created_at)).all()
        return [(r.id, OwnerIntent.model_validate(json.loads(r.intent_json))) for r in rows]

    def consume(self, record_id: str, decision_id: str) -> None:
        row = self.session.get(OwnerIntentRecord, record_id)
        if row is not None:
            row.consumed_by = decision_id
            self.session.flush()
