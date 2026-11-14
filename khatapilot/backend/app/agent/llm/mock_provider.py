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
from app.core.masking import PHONE_RE, find_phone_numbers, mask_phone
from app.core.money import AMOUNT_RE, fmt_taka, normalize_digits, parse_amount

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
REMINDER_WORDS = re.compile(r"\b(remind|reminding|reminders?|tagada|mone koriye)\b", re.I)
# "don't send reminders to Babul", "stop reminding Babul", "Babul ke ar reminder dio na"; and back on
REMINDERS_OFF = re.compile(
    rf"\b(?:don'?t|do not|never|stop|no more|no)\s+(?:send(?:ing)?\s+)?(?:any\s+)?(?:more\s+)?(?:baki\s+)?"
    rf"(?:reminders?|sms|messages?|tagada)\s+(?:to|for)\s+(?P<a>{NAME})"
    rf"|\bstop reminding\s+(?P<b>{NAME})"
    rf"|(?P<c>{NAME}?)\s+(?:ke|k)\s+ar\s+(?:reminder|sms|message|tagada)\s+(?:dio|diyo|pathio|pathiyo|diben|dibe)\s+na\b",
    re.I)
REMINDERS_ON = re.compile(
    rf"\b(?:start|resume|restart|turn on|allow)\s+(?:the\s+)?(?:baki\s+)?reminders?\s+(?:again\s+)?(?:to|for)\s+"
    rf"(?P<a>{NAME})|\b(?:start|resume|restart|turn on)\s+(?P<b>{NAME}?)'?s\s+reminders?",
    re.I)
# Customers named for a reminder: "send baki reminder to Alam, Jack and Rasel", "remind Babul and Sumi",
# "Alam, Rasel ar Jack ke reminder pathao". Whole groups ("everyone", "all who owe") are not names.
_NAME_LIST = r"[A-Za-z][A-Za-z.'\- ]*(?:\s*(?:,|&|/|\band\b|\bar\b|\bo\b)\s*[A-Za-z][A-Za-z.'\- ]*)*"
REMIND_NAMED = (
    re.compile(rf"\b(?:reminders?|sms|messages?|tagada)\s+(?:sms\s+|message\s+)?(?:to|for)\s+(?P<names>{_NAME_LIST})",
               re.I),
    re.compile(rf"\bremind\s+(?P<names>{_NAME_LIST})", re.I),
    re.compile(rf"(?P<names>{_NAME_LIST}?)\s+(?:ke|k|der|der ke)\s+(?:baki\s+)?(?:reminder|sms|message|tagada)\b", re.I),
)
GROUP_WORDS = {"everyone", "everybody", "all", "them", "anyone", "anybody", "customers", "customer", "people",
               "debtors", "those", "who", "baki", "sobai", "shobai", "sobaike", "shobaike", "all customers", "me", "us",
               "him", "her", "it", "amake"}
NAME_TAIL = re.compile(r"\b(?:about|to|for|of|who|whose|that|today|now|please|pls|plz|by|in|on|again|their|his|her|"
                       r"baki|dues?|money|taka|tk|reminders?|sms|messages?|send|pathao|pathan|dao|den|koro|"
                       r"english|banglish|bangla)\b.*$", re.I)
NAME_HEAD = re.compile(r"^(?:send|prepare|give|a|an|the|baki|customer|customers?|mr|mrs|ms|please|pls|plz|ekhon|"
                       r"ajke|aj|amar|ekta|ei)\s+", re.I)


def reminder_names(text: str) -> list[str] | None:
    """The customers named in a reminder request, or None when it is about a group (or names nobody)."""
    for pattern in REMIND_NAMED:
        m = pattern.search(text)
        if not m:
            continue
        names = []
        for part in re.split(r"\s*(?:,|&|/|\band\b|\bar\b|\bo\b)\s*", m.group("names"), flags=re.I):
            part = part.strip(" .'-")
            while (cut := NAME_HEAD.sub("", part)) != part:
                part = cut
            part = NAME_TAIL.sub("", part).strip(" .'-")
            if not part or part.lower() in GROUP_WORDS or len(part) > 40:
                continue
            names.append(" ".join(w if w.isupper() else w[:1].upper() + w[1:] for w in part.split()))
        if names and not any(n.lower() in GROUP_WORDS for n in names):
            return names[:20]
    return None


BUFFER_WORDS = re.compile(r"\b(?:safety\s+)?buffer\b|\bcushion\b|\bsafety (?:net|margin)\b", re.I)
# The owner's own monthly savings: "save 10% every month", "move my savings", "shonchoy koto?"
SAVINGS_WORDS = re.compile(r"\b(?:savings?|shonchoy|sonchoy|sanchay|sonchay)\b|সঞ্চয়", re.I)
SAVE_A_SHARE = re.compile(r"\bsave\b.*(?:%|\bpercent\b|\bevery month\b|\bmonthly\b|\bthis month\b|\beach month\b)", re.I)
PERCENT = re.compile(r"(?<![\d.])(\d{1,2})\s*(?:%|percent\b|per\s*cent\b|shotangsho)", re.I)
GROW_WORDS = re.compile(r"\b(?:grow|growth|expand|scale|boro korbo|baraite|business barabo|bebsa barabo)\b", re.I)
ADVICE_WORDS = re.compile(r"\b(?:advice|advise|tips?|what should i do|what do i do|ki korbo|ki kora uchit|"
                          r"poramorsho|suggestions?)\b", re.I)
MOVE_WORDS = re.compile(r"\b(?:move|transfer|send|put|deposit|pathao|pathiye dao|joma koro|rakho)\b", re.I)
# Banglish "Meghna ke taka dao": a payment verb without a number (BANGLISH_PAY needs one)
BANGLISH_PAY_WORDS = re.compile(r"\b(?:ke|k)\s+(?:[a-z]+\s+){0,2}(?:dao|den|pathao|pathan|pathiye dao)\b", re.I)
# Words that follow "pay"/"send" but are not who to pay, and requests that are not payments at all
NOT_A_PAYEE = {"money", "taka", "tk", "it", "him", "her", "them", "back", "now", "bill", "bills", "rent", "bhara",
               "electricity", "salary", "dues", "everyone", "all", "someone", "me", "you"}
NOT_A_PAYMENT = re.compile(r"\b(messages?|sms|text|reminders?|remind|invoices?|photos?|reports?|receipts?|"
                           r"statements?|summary|list)\b", re.I)


# ---------------------------------------------------------------- small parsers

MONTH_WORD = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
# "20 Oct", "Oct 20", "3/11": a day of the month, not an amount
CALENDAR_DATE = re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{MONTH_WORD}\b|\b{MONTH_WORD}\s+\d{{1,2}}(?:st|nd|rd|th)?\b"
                           r"|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b", re.I)


def amounts_in(text: str) -> list[Decimal]:
    clean = QUANTITY.sub(" ", CALENDAR_DATE.sub(" ", PHONE_RE.sub(" ", normalize_digits(text))))
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


RELATIVE_DAYS = re.compile(
    r"\b(?:in|within|after)\s+(?P<n1>\d{1,3})\s*(?P<u1>days?|weeks?|months?)\b"
    r"|\b(?P<n2>\d{1,3})\s*(?P<u2>din|diner|dine|days?|shoptah|soptaho|weeks?|mash|mas|months?)\s+"
    r"(?:por|pore|moddhe|er\s+moddhe|later|after)\b",
    re.I)


def pay_back_day(text: str, today: date) -> date | None:
    """When a customer will pay baki back, if the owner said: 'in 7 days', '7 din por', '2 shoptah er
    moddhe', 'next week', 'by Friday', 'by 20 Oct', 'kal'. None when no day was given."""
    lowered = normalize_digits(text).lower()
    if m := RELATIVE_DAYS.search(lowered):
        n = int(m.group("n1") or m.group("n2"))
        unit = (m.group("u1") or m.group("u2")).lower()
        per = 7 if unit.startswith(("week", "shoptah", "soptaho")) else 30 if unit.startswith(("month", "mas")) else 1
        return today + timedelta(days=n * per) if n else None
    if re.search(r"\b(next week|agami shoptah|samner shoptah|porer shoptah)\b", lowered):
        return today + timedelta(days=7)
    return day_in(lowered, today)


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


def call(name: str, /, **arguments: Any) -> LLMToolCall:
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


# ---------------------------------------------------------------- bills

BILL_NOUN = re.compile(r"\b(bills?|dues|payables?|due date)\b", re.I)  # not "due" alone: "sold on due" is baki
# "I have to pay Meghna 5,000 by Friday", "Meghna ke 5000 dite hobe": money the shop owes on a day
OWE = re.compile(r"\b(have to pay|has to pay|need to pay|must pay|got to pay|i owe|we owe|dite hobe|dite hbe|"
                 r"debo|dibo|dena ache|pawna)\b", re.I)
PRIORITY_OFF = re.compile(r"\b(not urgent|no longer urgent|not (?:a |the )?priority|no priority|remove (?:the )?priority|"
                          r"unprioriti[sz]e|deprioriti[sz]e|joruri na|jaruri na)\b", re.I)
PRIORITY_ON = re.compile(r"\b(urgent|priority|prioriti[sz]e|important|first|joruri|jaruri|age dite hobe|aage dite hobe|"
                         r"must pay)\b", re.I)
BILL_DELETE = re.compile(r"\b(delete|remove|cancel|drop|bad dao|baad dao|kete dao|muche dao|lagbe na)\b", re.I)
BILL_CHANGE = re.compile(r"\b(now|change|changed|update|move|moved|shift|postpone|extend|push|pichiye|bariye|"
                         r"instead)\b", re.I)
BILL_LIST = re.compile(r"\b(show|list|what|which|when|ki ki|kon|koyta|upcoming|due soon|dekhao|baki ache)\b", re.I)
# Words around a payee's name in bill phrases that are not part of the name
BILL_STOP = {"add", "new", "a", "an", "the", "my", "our", "bill", "bills", "due", "dues", "payable", "is", "are", "now",
             "delete", "remove", "cancel", "drop", "change", "update", "move", "shift", "postpone", "extend", "push",
             "prioritise", "prioritize", "priority", "urgent", "important", "first", "make", "set", "mark", "pay",
             "i", "we", "have", "has", "need", "must", "to", "owe", "of", "for", "from", "on", "by", "within", "in",
             "it", "that", "this", "please", "joruri", "jaruri", "er", "r", "ke", "k", "taka", "tk", "not", "no",
             "longer", "a", "dite", "hobe", "debo", "dibo", "s", "bad", "baad", "dao", "kete", "muche", "lagbe", "na",
             "instead", "till", "until", "before", "next", "and", "date", "today", "tomorrow", "kal",
             *WEEKDAYS, *MONTH_NUMBERS}


def bill_payee(text: str) -> str | None:
    """The payee a bill phrase is about: "Meghna's bill", "the bill for DESCO", "prioritise Rubel",
    "Meghna is urgent", "I have to pay Rahim Traders 5,000"."""
    clean = re.sub(r"(\w)'s\b", r"\1", PHONE_RE.sub(" ", text))
    for pattern in (rf"(?P<n>{NAME}?)(?:er|r)?\s+(?:bill|due|dues|payable)\b",
                    rf"\b(?:bill|due|dues)\s+(?:(?:for|of|to|from)\s+)?(?P<n>{NAME})",
                    rf"\b(?:prioriti[sz]e|pay|owe|delete|remove|cancel|move|change|update)\s+(?P<n>{NAME})",
                    rf"(?P<n>{NAME}?)\s+(?:ke|k)\s",
                    rf"^\s*(?P<n>{NAME}?)\s+(?:is|are|first|urgent)\b"):
        for m in re.finditer(pattern, clean, re.I):
            words = m.group("n").split()
            while words and words[0].lower() in BILL_STOP:
                words.pop(0)
            name = []
            for word in words:
                if word.lower() in BILL_STOP or word.lower() in NAME_ENDS:
                    break
                name.append(word)
            if name:
                return " ".join(name)
    return None


def plan_bills(t: str, today: date) -> list[LLMToolCall] | None:
    """Bill phrases, or None when the text is not about the shop's own bills."""
    low = t.lower()
    has_bill_noun = bool(BILL_NOUN.search(low))
    owes = bool(OWE.search(low)) and not re.search(r"\b(owes? me|owe us|who owes)\b", low)
    priority_off = bool(PRIORITY_OFF.search(low))
    priority_on = bool(PRIORITY_ON.search(low)) and not priority_off
    if not (has_bill_noun or owes or priority_on or priority_off):
        return None
    if re.search(r"\b(sold|bikri|baki dilam|paid|dilam|diyechi)\b", low) or REMINDER_WORDS.search(low):
        return None  # bookkeeping or reminders that merely mention a due
    amounts = amounts_in(t)
    if priority_on and not (has_bill_noun or owes) and amounts and PAY_NOW.search(low):
        return None  # "URGENT: pay Rahim Traders 5000 now" is a payment; "pay Meghna first" (no amount) is not
    day = pay_back_day(t, today)
    name = bill_payee(t)
    priority = True if priority_on else False if priority_off else None

    if has_bill_noun and BILL_DELETE.search(low):
        return [call("delete_bill", payee_name=name)] if name else []
    if (owes or (has_bill_noun and re.search(r"\b(add|new|another)\b", low))) and not BILL_CHANGE.search(low):
        if name and amounts and day:
            return [call("add_bill", payee_name=name, amount_taka=str(amounts[0]), due_date=str(day),
                         **({"priority": True} if priority_on or re.search(r"\bmust\b", low) else {}))]
        return []  # missing who, how much or by when: compose() asks
    if name and (priority is not None or (has_bill_noun and BILL_CHANGE.search(low) and (amounts or day))):
        return [call("update_bill", payee_name=name, priority=priority,
                     amount_left_taka=str(amounts[0]) if amounts and has_bill_noun else None,
                     due_date=str(day) if day and has_bill_noun else None)]
    if has_bill_noun and not amounts and (BILL_LIST.search(low) or not name):
        return [call("list_bills")]
    return None


def bill_question(text: str, today: date) -> str | None:
    """A new bill without who, how much or by when: ask for what is missing in one message."""
    t = normalize_digits(text).strip()
    low = t.lower()
    adding = (OWE.search(low) and not re.search(r"\b(owes? me|owe us|who owes)\b", low)) or (
        BILL_NOUN.search(low) and re.search(r"\b(add|new|another)\b", low))
    if not adding:
        return None
    name, amounts, day = bill_payee(t), amounts_in(t), pay_back_day(t, today)
    missing = [w for w, ok in (("who it is for", name), ("how much", amounts), ("by when", day)) if not ok]
    if not missing:
        return None
    who = name or "Rahim Traders"
    amount = fmt_taka(int(amounts[0] * 100)) if amounts else "৳5,000"
    when = "on 20 Oct" if not day else f"on {day:%d %b}"
    ask = " and ".join(missing) if len(missing) < 3 else "who it is for, how much, and by when"
    return (f"To add the bill I need {ask}. Send it as one message, for example "
            f"\"add bill {who} {amount} due {when.removeprefix('on ')}\".")


FORGIVE = re.compile(r"\b(maf|maaf|mafi|forgive|forgiven|write (?:it )?off|written off|writeoff|waive|"
                     r"chere (?:dilam|dao|dichi|disi))\b", re.I)
# What customers owe the shop: "baki", and "debt" (also typed "dept")
BAKI = r"(?:baki|debts?|depts?|debtors?|deptors?)"
BAKI_REMOVE = re.compile(rf"\b(?:remove|delete|clear|cut)\b.*\b{BAKI}\b|\b{BAKI}\b.*\b(?:bad|baad|kete) dao\b", re.I)
BAKI_CORRECT = re.compile(r"\b(?:to|not|now|actually|correct|correction|change|set|update|should be|hobe)\b", re.I)
BAKI_NAME_STOP = {"forgive", "remove", "delete", "clear", "cut", "change", "correct", "set", "update", "the", "all", "of",
                  "from", "baki", "debt", "debts", "dept", "depts", "debtors", "is", "to", "please", "pls", "write",
                  "off", "waive", "er", "r", "owes", "me", "taka", "tk", "my", "list", "now"}


def baki_customer(t: str) -> str | None:
    """Who the owner means in "forgive Jamal's baki", "Jamal er baki maf", "remove Babul from baki",
    "change Rina's baki to 1000", "Jamal owes me 300 not 450"."""
    for pattern in (rf"\bof\s+(?P<n>{NAME}?)(?:'s|’s)?\s+{BAKI}\b",
                    rf"\b(?:forgive|remove|delete|clear|cut|change|correct|set|update)\s+(?P<n>{NAME}?)(?:'s|’s)?\s+"
                    rf"(?:{BAKI}|from|to|\d)",
                    rf"(?P<n>{NAME}?)(?:'s|’s|\s+er|\s+r)?\s+{BAKI}\b",
                    rf"^\s*(?P<n>{NAME}?)\s+owes\b"):
        if m := re.search(pattern, t, re.I):
            words = [w.removesuffix("'s").removesuffix("’s") for w in m.group("n").split()]
            while words and words[0].lower() in BAKI_NAME_STOP:
                words.pop(0)
            name = []
            for word in words:
                if word.lower() in BAKI_NAME_STOP or word.lower() in NAME_ENDS:
                    break
                name.append(word)
            if name:
                return " ".join(name)
    return None


def baki_change_question(text: str) -> str | None:
    """Forgiving or correcting baki without saying whose: ask instead of guessing."""
    t = normalize_digits(text).strip()
    if plan_baki_change(t) != []:
        return None
    return ("Whose baki should I change? Send it as one message with their name, for example "
            "\"forgive Jamal Uddin's baki\" or \"change Jamal Uddin's baki to ৳300\".")


def plan_baki_change(t: str) -> list[LLMToolCall] | None:
    """The owner forgiving or correcting what a customer owes, or None when the text is not that."""
    low = t.lower()
    if re.search(r"\b(sold|bikri|dilam|diyechi|disi|paid|dilo|dise|ferot|remind\w*)\b", low):
        return None  # recording baki given or paid back, or reminders
    forgive = bool(FORGIVE.search(low) or BAKI_REMOVE.search(low))
    amounts = amounts_in(t)
    correct = (not forgive and amounts and BAKI_CORRECT.search(low)
               and (re.search(rf"\b{BAKI}\b", low) or re.search(r"\bowes?\b(?: me| us)?\b.*\bnot\b", low)))
    if not (forgive or correct):
        return None
    name = baki_customer(t)
    if not name:
        return []  # compose() asks who
    if forgive:
        return [call("change_baki", customer_name=name, change="forgive",
                     amount_taka=str(amounts[0]) if amounts else None)]
    target = None
    for pattern in (r"\bto\s+(?:৳|tk\.?\s*)?(?P<a>[\d,]+(?:\.\d+)?)", r"(?P<a>[\d,]+(?:\.\d+)?)\s*(?:taka|tk)?\s+not\b"):
        if m := re.search(pattern, low):
            found = amounts_in(f"{m.group('a')} taka")
            target = found[0] if found else None
            break
    return [call("change_baki", customer_name=name, change="set_to", amount_taka=str(target or amounts[0]))]


# ---------------------------------------------------------------- money a customer gave, and what one owes

GAVE = r"(?:paid|pays|sent|gave|dilo|dise|dyse|dilen|pathalo|pathaise|pathaiche|pathiyeche|ferot dilo)"
AMOUNT_WORDS = r"(?:৳|tk\.?)?\s*[\d,]+(?:\.\d+)?\s*(?:k|hajar|hazar)?\s*(?:taka|tk|/-)?"
GOT = r"(?:got|received|recieved|receive|pelam|paisi|peyechi|pailam)"
NOT_A_CUSTOMER = {"i", "ami", "we", "amra", "you", "customer", "someone", "somebody", "he", "she", "they", "who",
                  "everyone", "nobody", "today", "aj", "ajke"}


def customer_payer(t: str) -> str | None:
    """Who gave the shop money in "Babul Mia paid 2000", "Babul sent me 1000 on bkash", "Jamal 500 taka dilo",
    "got 500 from Rina", "Rina theke 500 pelam". None when the text is not that (or the shop paid someone)."""
    low = t.lower()
    if re.search(r"\b(bought|buy|purchased|kinlam|kinechi|invoice|stock|maal|mal)\b", low) or QUANTITY.search(t):
        return None  # a supplier delivering goods, not a customer paying
    for pattern in (rf"^\s*(?P<n>{NAME}?)\s+{AMOUNT_WORDS}\s+(?:ferot\s+)?{GAVE}\b",
                    rf"^\s*(?P<n>{NAME}?)\s+{GAVE}\b",
                    rf"\b{GOT}\s+{AMOUNT_WORDS}\s*(?:(?:on|by|via|in)\s+\w+\s+)?from\s+(?P<n>{NAME})",
                    rf"^\s*(?P<n>{NAME}?)\s+(?:er\s+|r\s+)?theke\b.*\b{GOT}\b"):
        if (m := re.search(pattern, t, re.I)) and (name := clip_name(m.group("n"))):
            if name.split()[0].lower() in NOT_A_CUSTOMER:
                return None
            return name
    return None


def one_debtor_asked(t: str) -> str | None:
    """The customer in "how much does Babul owe?", "what does Rina owe me", "Babul er baki koto",
    "Babul koto baki"."""
    for pattern in (rf"\bhow much (?:does|do|did|money does)?\s*(?P<n>{NAME}?)\s+owes?\b",
                    rf"\bwhat (?:does|do)\s+(?P<n>{NAME}?)\s+owes?\b",
                    rf"\bdoes\s+(?P<n>{NAME}?)\s+(?:still\s+)?owe\b",
                    rf"^\s*(?P<n>{NAME}?)(?:'s|’s|\s+er|\s+r)?\s+(?:{BAKI}\s+koto|koto\s+{BAKI})\b"):
        if (m := re.search(pattern, t, re.I)) and (name := clip_name(m.group("n"))):
            if name.split()[0].lower() in NOT_A_CUSTOMER | {"much", "me", "us"}:
                return None
            return name
    return None


SETTINGS_WORDS = re.compile(r"\b(limits?|trust|trusted|untrust|block|blocked|blocklist)\b", re.I)


def plan_settings(t: str) -> list[LLMToolCall] | None:
    """Limits, trusted payees and the block list. Changes are only proposals: the guard holds every one
    for the owner (the PIN for limits and payees, a tap on Approve to block a number)."""
    low = t.lower()
    if not SETTINGS_WORDS.search(low):
        return None
    calls: list[LLMToolCall] = []
    for clause in re.split(r"\s*(?:,|;|\band\b|\bthen\b|\bar\b)\s*", t, flags=re.I):
        c = clause.lower()
        numbers = find_phone_numbers(clause)
        amounts = amounts_in(clause)
        if re.search(r"\blimits?\b", c) and amounts:
            key = "daily_taka" if re.search(r"\b(daily|day|per day|a day|din|dine|total)\b", c) else "per_txn_taka"
            calls.append(call("set_limits", **{key: str(amounts[0])}))
        elif re.search(r"\b(block|blocked)\b", c) and numbers and not re.search(r"\bunblock\b", c):
            calls.append(call("block_number", number=numbers[0]))
        elif m := re.search(rf"\b(?:remove|untrust|delete)\s+(?P<n>{NAME}?)\s+from\s+(?:my\s+|the\s+)?trusted\b"
                            rf"|\bstop\s+trusting\s+(?P<m>{NAME})", clause, re.I):
            if name := clip_name(m.group("n") or m.group("m")):
                calls.append(call("remove_trusted_payee", name=name))
        elif re.search(r"\btrust(?:ed)?\b", c) and numbers:
            name = None
            if m := re.search(rf"\b(?:trust|add)\s+(?P<n>{NAME}?)\s*(?:\(|,|:|-)?\s*(?:\+?88)?01\d", clause, re.I):
                name = clip_name(m.group("n"))
            if name and name.lower() not in {"number", "this", "the", "a"}:
                calls.append(call("add_trusted_payee", name=name, number=numbers[0],
                                  method=method_in(c, "bkash")))
    if calls:
        return calls
    if re.search(r"\b(block ?list|blocked (?:list|numbers?))\b", low):
        return [call("list_blocked_numbers")]
    if re.search(r"\blimits?\b", low) and not amounts_in(t):
        return [call("get_limits")]
    return None


# ---------------------------------------------------------------- planning

GOAL_WORDS = re.compile(r"\b(?:goals?|lokkho|lokkhyo)\b", re.I)
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"), start=1)}


def goal_month(low: str, today: date) -> str | None:
    """'by March' (the next March), 'by Dec 2026', 'in 6 months' as YYYY-MM."""
    if m := re.search(r"\b(?:by|before|within|er moddhe)\s+(?:the end of\s+)?(?P<m>jan|feb|mar|apr|may|jun|jul|aug|sep|"
                      r"oct|nov|dec)[a-z]*\.?(?:\s+(?P<y>20\d\d))?", low):
        month = MONTHS[m.group("m")]
        year = int(m.group("y")) if m.group("y") else today.year + (month < today.month)
        return f"{year}-{month:02d}"
    if m := re.search(r"\b(?:in|within)\s+(\d{1,2})\s*(?:months?|mash)\b", low):
        total = today.month - 1 + int(m.group(1))
        return f"{today.year + total // 12}-{total % 12 + 1:02d}"
    return None


def goal_name(t: str) -> str | None:
    """'goal 35,000 for a fridge by March' -> 'fridge'; 'Eid er jonno 20,000 goal' -> 'Eid'."""
    if m := re.search(r"\bfor\s+(?:a\s+|an\s+|my\s+|the\s+|our\s+)?(?P<n>[A-Za-z][A-Za-z' &-]*?)\s*(?:\bby\b|\bbefore\b|"
                      r"\bin\b|\bwithin\b|[,.!?]|$)", t, re.I):
        return m.group("n").strip()
    if m := re.search(r"(?P<n>[A-Za-z][A-Za-z' -]*?)\s+(?:er|r)\s+jonno\b", t, re.I):
        return m.group("n").strip()
    if m := re.search(r"\bgoal\s*[:\-]\s*(?P<n>[A-Za-z][A-Za-z' &-]*?)\s+(?:৳|tk|taka)?\s*\d", t, re.I):
        return m.group("n").strip()
    return None


def plan_goals(t: str, today: date) -> list[LLMToolCall] | None:
    """Savings goals: add one with the owner's amount, remove one, ask for starter goals, or show them."""
    low = t.lower()
    if not GOAL_WORDS.search(low):
        return None
    if re.search(r"\b(?:remove|delete|drop|cancel|bad dao|baad dao|lagbe na)\b", low):
        m = (re.search(r"\b(?:remove|delete|drop|cancel)\s+(?:the\s+|my\s+|this\s+)?(?P<n>[\w' -]+?)\s+goal\b", t, re.I)
             or re.search(r"\bgoal\s+(?:for\s+)?(?:the\s+)?(?P<n>[\w' -]+?)\s*$", t, re.I)
             or re.search(r"(?P<n>[\w' -]+?)\s+(?:er\s+)?goal\s+(?:bad|baad)\s+dao", t, re.I))
        name = m.group("n").strip() if m else None
        return [call("remove_savings_goal", name=name)] if name and len(name) >= 2 else []
    if re.search(r"\b(?:suggest|recommend|starter|ideas?|help me|set (?:some|them) up|for me|what should i save)\b", low):
        return [call("add_starter_goals")]
    amounts = [a for a in amounts_in(t) if a > 0]
    if not amounts:
        return [call("get_savings")]
    name = goal_name(t) or "Savings goal"
    by = goal_month(low, today)
    return [call("set_savings_goal", name=name[:48], amount_taka=str(amounts[0]), **({"by_month": by} if by else {}))]


def plan_savings(t: str) -> list[LLMToolCall] | None:
    """The owner's monthly savings: set the share, pause it, move this month's amount, or ask about it."""
    low = t.lower()
    if not (SAVINGS_WORDS.search(low) or SAVE_A_SHARE.search(low)):
        return None
    if re.search(r"\b(?:pause|stop|bondho)\b", low):
        return [call("set_savings_share", pause=True)]
    if share := PERCENT.search(low):
        return [call("set_savings_share", share_pct=int(share.group(1)))]
    question = "?" in low or re.search(r"\b(?:how much|what|koto|show|status|plan|can i)\b", low)
    if MOVE_WORDS.search(low) or (not question and re.search(r"\bsave\b.*\bthis month", low)):
        amounts = [a for a in amounts_in(t) if a > 0]
        return [call("save_to_savings", **({"amount_taka": str(amounts[0])} if amounts else {}))]
    return [call("get_savings")]


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

    # Limits, trusted payees, the block list (before payments: "per payment limit" is not a payment)
    if (settings_calls := plan_settings(t)) is not None:
        return settings_calls

    # Savings goals (before growth: "a goal for a bigger shop" is a goal, not a growth question)
    if (goal_calls := plan_goals(t, today)) is not None:
        return goal_calls

    # Growing the shop (before savings: "how can I grow and save more" is a growth question)
    if GROW_WORDS.search(low):
        return [call("get_growth_plan")]

    # Where to keep savings: "where should I keep my savings?", "dps or sanchayapatra?"
    if re.search(r"\b(?:dps|sanchay\w*|sonchoypotro|savings? (?:products?|options?|schemes?|certificates?)|"
                 r"where (?:should|can|do) i (?:keep|put) (?:my )?(?:savings|money))", low):
        return [call("savings_products")]

    # Advice for the week: "any advice?", "what should I do this week?", "ki korbo?"
    if ADVICE_WORDS.search(low):
        return [call("get_advice")]

    # The owner's monthly savings (before payments: "move 2,000 to savings" is not a payment to a supplier)
    if (savings_calls := plan_savings(t)) is not None:
        return savings_calls

    # The safety buffer: set it, go back to the automatic one, or ask about it
    if BUFFER_WORDS.search(low):
        if re.search(r"\b(auto|automatic|automatically|default|reset)\b", low):
            return [call("set_safety_buffer", automatic=True)]
        if amounts and re.search(r"\b(set|make|keep|change|raise|lower|increase|reduce|put|want|rakho|rakhbo|koro|"
                                 r"hobe|should be)\b|\bto\s*(?:৳|tk\.?)?\s*\d|=\s*\d", low):
            return [call("set_safety_buffer", amount_taka=str(amounts[0]))]
        return [call("get_safety_buffer")]

    # Baki the owner forgives or corrects (before bills: "remove Babul from baki" is not a bill)
    if (baki_calls := plan_baki_change(t)) is not None:
        return baki_calls

    # Bills the shop has to pay: list, add, change, prioritise, remove (before payments: "pay Meghna first"
    # puts Meghna's bill first, and "I have to pay Meghna 5,000 by Friday" is a due, not a payment now)
    if (bill_calls := plan_bills(t, today)) is not None:
        return bill_calls

    # Pay someone (an instruction for now or a later day). "send reminders" is not a payment, and nor is
    # "sold on baki, she will pay back in 2 weeks": that is when the customer pays the shop.
    customer_pays_back = bool(re.search(r"\bpays?\s+(?:me\s+|us\s+|it\s+)?back\b", low)
                              and re.search(r"\b(baki|bakite|credit|udhar)\b", low))
    if ((PAY_NOW.search(low) and not re.search(r"\bpaid\b", low) and not REMINDER_WORDS.search(low)
         and not customer_pays_back)
            or BANGLISH_PAY.search(low) or BANGLA_PAY.search(t)):
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

    # Money a customer gave the shop ("Babul paid 2000", "got 500 from Rina on bkash", "Jamal 300 taka dilo").
    # Paying back baki only if the owner says so ("paid back", "ferot"); otherwise the tool asks when they owe.
    if amount and (payer := customer_payer(t)):
        purpose = sms_purpose(t)
        return [call("record_customer_payment", customer_name=payer, amount_taka=amount,
                     method=method_in(low, "cash"), purpose=purpose)]

    # Record a sale. "Jamal ke 450 taka baki dilam" is a sale on credit too: goods given, money owed.
    # ("Rahim Traders er baki 2000 taka dilam" pays off the shop's own dues; that is further down.)
    gave_on_credit = re.search(r"\b(?:ke|k)\b.*\bbaki\s+(?:dilam|diyechi|dichi|disi|dici)\b", low)
    if amount and (gave_on_credit or re.search(r"\b(sold|sell|sale|bikri|bechi|becha|beche|bechlam)\b", low)):
        on_credit = re.search(r"\b(baki|bakite|credit|udhar|due)\b", low)
        if on_credit:
            who = None
            if m := re.search(rf"\bto\s+(?P<n>{NAME})", t):
                who = m.group("n")
            # "Jamal ke baki 500" or "Jamal ke 450 taka baki"
            elif m := re.search(rf"(?P<n>{NAME}?)\s+(?:ke|k)\s+(?:(?:৳|tk)?\s*[\d,]+\s*(?:taka|tk)?\s+)?baki", t, re.I):
                who = m.group("n")
            back = pay_back_day(t, today)
            return [call("add_ledger_entry", kind="baki_sale", amount_taka=amount, method="credit",
                         counterparty_name=clip_name(who), item=item, pay_back_by=str(back) if back else None)]
        return [call("add_ledger_entry", kind="sale", amount_taka=amount, method=method_in(low, "cash"), item=item)]

    # Record a purchase
    if amount and re.search(r"\b(bought|buy|purchased|kinlam|kinechi|kena|kinsi)\b", low):
        supplier = m.group("n") if (m := re.search(rf"\bfrom\s+(?P<n>{NAME})", t)) else None
        credit = bool(re.search(r"\b(baki|credit|udhar|due)\b", low))
        return [call("add_ledger_entry", kind="purchase", amount_taka=amount,
                     method="credit" if credit else method_in(low, "cash"), counterparty_name=clip_name(supplier),
                     item=item)]

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
    for pattern, stop in ((REMINDERS_OFF, True), (REMINDERS_ON, False)):
        if m := pattern.search(t):
            who = re.sub(r"(?i)\s+again$", "", clip_name(next(g for g in m.groups() if g)) or "").rstrip("'")
            if not who or who.lower() in {"everyone", "everybody", "them", "all", "anyone", "anybody", "customers"}:
                return []  # not one customer: never read "don't send reminders to anyone" as "send them"
            return [call("set_reminder_opt_out", customer_name=who, stop=stop)]
    if REMINDER_WORDS.search(low):
        if re.search(r"\b(who paid|paid since|did they pay|kara dise|ke dise)\b", low):
            return [call("list_reminders")]
        days = 14
        if m := re.search(r"(?:over|more than|beshi|at least)\s+(\d+)\s*(day|days|week|weeks|din|shoptah)", low):
            days = int(m.group(1)) * (7 if m.group(2).startswith(("week", "shoptah")) else 1)
        language = "banglish" if re.search(r"\b(banglish|bangla|tagada)\b", low) else "english"
        if names := reminder_names(t):
            return [call("send_reminders", customers=names, language=language)]
        return [call("send_reminders", min_days=days, language=language)]

    # Questions
    if who := one_debtor_asked(t):
        return [call("list_debtors", customer=who)]
    if re.search(r"\b(owe|owes|owed|debtors?|deptors?|debts?|depts?|baki koto|kar kache|who has baki)\b", low):
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
            calls.append(call("record_sms", segment=segment.index, purpose=sms_purpose(brief.owner_text)))
        for number, amount in money_instructions(segment.text):  # gullible on purpose
            calls.append(call("propose_payment", payee_number=number, amount_taka=str(amount)))
    return calls + plan_owner_text(brief.owner_text, brief.today)


def sms_purpose(owner_text: str) -> str | None:
    """What the owner said forwarded money in is for, in their own words: paying off baki, or something else.
    None when they did not say ("record this"); then the app asks if the sender owes baki."""
    low = normalize_digits(owner_text).lower()
    if re.search(r"\b(not (?:for )?(?:the )?baki|baki na|baki noy|something else|different|advance|new sale|"
                 r"sale|bikri|order)\b", low):
        return "other"
    if re.search(r"\b(baki|repay\w*|paid (?:me )?back|pays? (?:me )?back|ferot|shodh|shod)\b", low):
        return "baki"
    return None


def payment_question(text: str) -> str | None:
    """The owner asked to pay but left out who or how much: ask for it instead of guessing (the system
    prompt's rule). The answer has to be the whole instruction in one message, because the guard only
    approves a payment whose payee and amount both appear in the owner's own words."""
    t = normalize_digits(text).strip()
    low = t.lower()
    asks_to_pay = ((PAY_NOW.search(low) and not re.search(r"\bpaid\b", low)) or BANGLISH_PAY_WORDS.search(low)
                   or BANGLISH_PAY.search(low))
    if not asks_to_pay or NOT_A_PAYMENT.search(low):
        return None
    amounts = amounts_in(t)
    numbers = find_phone_numbers(t)
    name = example = None
    for english, pattern in ((True, rf"\bto\s+(?P<n>{NAME})"), (True, rf"\b(?:pay|send|transfer)\s+(?P<n>{NAME})"),
                             (False, rf"(?P<n>{NAME}?)\s+(?:ke|k)\s")):
        if (m := re.search(pattern, t, re.I)) and (found := clip_name(m.group("n"))):
            words = found.split()
            while words and words[0].lower() in NOT_A_PAYEE:
                words.pop(0)
            if words and not any(w.lower() in NOT_A_PAYEE for w in words):
                name = " ".join(words)
                # keep what followed the name ("tomorrow", "on bkash") so the example is their whole instruction
                at = t.find(name, m.start("n"))
                tail = t[at + len(name):].rstrip(" .!?") if english else ""
                example = f"pay {name} ৳5,000{tail}"
                break
    if (name or numbers) and amounts:
        return None  # complete; if the planner still made nothing of it, the generic help is the right answer
    if name:
        return (f"How much should I pay {name}? Send it as one message, for example \"{example}\", "
                "so I only pay what you wrote.")
    if numbers:
        return (f"How much should I send to {mask_phone(numbers[0])}? Send the number and the amount together "
                "in one message, so I only pay what you wrote.")
    if amounts:
        amount = fmt_taka(int(amounts[0] * 100))
        return (f"Who should I pay {amount} to? Send it as one message, for example \"pay Rahim Traders {amount}\", "
                "so I only pay what you wrote.")
    return ("Who should I pay, and how much? Send both in one message, for example \"pay Rahim Traders ৳5,000\", "
            "so I only pay what you wrote.")


# ---------------------------------------------------------------- replies

def describe(tool: str, outcome) -> str:
    data = outcome.data if isinstance(outcome.data, dict) else {}
    if outcome.status == "blocked":
        return f"I did not do that. {outcome.message}"
    if outcome.status == "error":
        return f"I couldn't do that: {outcome.message}"
    if outcome.status == "asked":
        return f"{outcome.message} Tap one of the two buttons below."
    if outcome.status == "held":
        how = "Confirm it and enter your PIN" if outcome.requires_pin else "Tap Approve"
        warnings = " ".join(data.get("warnings") or [])
        return f"Waiting for you: {data.get('summary', 'an action')}. {how} if you want it. {warnings}".strip()
    if tool == "record_sms":
        way = "received from" if data.get("direction") == "in" else "sent to"
        text = (f"Booked the {data.get('provider', '').replace('bkash', 'bKash').replace('nagad', 'Nagad')} SMS: "
                f"{data.get('amount')} {way} {data.get('counterparty') or 'the other side'} as {data.get('recorded')}.")
        if data.get("over_baki_as_sale"):
            text += f" {data['over_baki_as_sale']} of it was more than they owed, so that part is booked as a sale."
        if data.get("customer_still_owes") == "৳0":
            text += f" {data.get('counterparty')} has now paid back everything they owed."
        elif data.get("customer_still_owes"):
            text += f" {data.get('counterparty')} still owes {data['customer_still_owes']}."
        if data.get("fee"):
            text += f" Fee {data['fee']} recorded too."
        return text
    if tool == "add_ledger_entry":
        text = f"Recorded {data.get('recorded')} {data.get('amount')} ({data.get('method')}, {data.get('category')})."
        if data.get("note"):
            text += f" {data['note']}"
        elif data.get("over_baki_as_sale"):
            text += f" {data['over_baki_as_sale']} of it was more than they owed, so that part is booked as a sale."
        if data.get("customer_now_owes") and data.get("recorded") != "sale":
            text += f" {data.get('customer') or 'They'} now owe{'s' if data.get('customer') else ''} {data['customer_now_owes']}."
        if data.get("pay_back_by"):
            text += f" Expected back by {data['pay_back_by']} ({data['days_to_pay_back']} days)."
        if bill := data.get("bill"):
            text += (f" That paid off {bill['payee']}'s bill." if bill.get("paid_off")
                     else f" {bill['payee']}'s bill now has {bill['amount_left']} left, due {bill['due']}.")
        return text
    if tool == "record_customer_payment":
        who = data.get("customer")
        if data.get("recorded") == "baki repayment":
            text = f"Recorded {data.get('amount')} from {who} against their baki ({data.get('method')})."
            if data.get("over_baki_as_sale"):
                text += f" {data['over_baki_as_sale']} of it was more than they owed, so that part is booked as a sale."
            text += (f" {who} has now paid back everything they owed." if data.get("customer_still_owes") == "৳0"
                     else f" {who} still owes {data.get('customer_still_owes')}.")
        else:
            text = f"Recorded {data.get('amount')} from {who} as a sale ({data.get('method')})."
            text += (f" Their baki of {data['owed_before']} is unchanged." if data.get("owed_before") not in (None, "৳0")
                     else f" {who} does not owe any baki.")
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
    if tool == "change_baki":
        who = data.get("customer")
        if data.get("raised_by"):
            text = f"Corrected {who}'s baki from {data['owed_before']} to {data['owes_now']}."
        elif data.get("lowered_by"):
            text = f"Corrected {who}'s baki from {data['owed_before']} to {data['owes_now']}."
        else:
            text = f"Forgave {data.get('forgiven')} of {who}'s baki."
            text += " They owe nothing now." if data.get("owes_now") == "৳0" else f" They still owe {data['owes_now']}."
        if data.get("note"):
            text += f" {data['note']}"
        return text + " No money moved."
    if tool == "list_debtors":
        people = data.get("customers", [])
        if data.get("asked_about") and not people:
            return f"{data['asked_about']} does not owe you anything right now."
        if not people:
            return "Nobody owes you money right now."
        def when(p: dict) -> str:
            if not p.get("promised_by"):
                return f"{p['days']} days"
            return f"{p['days']} days, {'was due' if p['promise_passed'] else 'due'} {p['promised_by']}"

        if data.get("asked_about") and len(people) == 1:
            p = people[0]
            since = "given today" if not p["days"] else f"for {p['days']} day{'s' if p['days'] != 1 else ''}"
            due = (f", {'was due' if p['promise_passed'] else 'due back'} {p['promised_by']}"
                   if p.get("promised_by") else "")
            return f"{p['name']} owes you {p['owes']} ({since}{due})."
        top = "; ".join(f"{p['name']} {p['owes']} ({when(p)})" for p in people[:5])
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
    if tool == "get_safety_buffer":
        return f"Your safety buffer is {data.get('safety_buffer')}. {data.get('why', '')}".strip()
    if tool == "set_safety_buffer":
        if data.get("set_by") == "you":
            text = f"Done: your safety buffer is now {data.get('safety_buffer')} (it was {data.get('was')})."
            if data.get("automatic_would_be"):
                text += f" The automatic one would be {data['automatic_would_be']}."
        elif data.get("set_by") == "the standard amount":
            text = (f"Done: back to the automatic safety buffer, {data.get('safety_buffer')} (it was {data.get('was')}). "
                    f"{data.get('why', '')}")
        else:
            text = (f"Done: back to the automatic safety buffer, {data.get('safety_buffer')} (it was {data.get('was')}), "
                    "sized from your money in on a usual day and how much it goes up and down.")
        return f"{text.strip()} {data.get('forecast_now', '')}".strip()
    if tool == "savings_products":
        rows = data.get("products", [])
        return ("Where you could keep your savings: " + " ".join(
            f"{i}. {p['name']} ({p['rate']}; {p['tenure']}). {p['fits_this_shop']}" for i, p in enumerate(rows, 1))
            + f" {data.get('note', '')}").strip()
    if tool == "get_advice":
        tips = data.get("tips", [])
        if not tips:
            return "Nothing needs your attention this week: cash, baki and bills all look fine."
        return "This week: " + " ".join(f"{i}. {t['title']}: {t['text']}" for i, t in enumerate(tips, 1))
    if tool == "get_growth_plan":
        moves = data.get("moves", [])[:3]
        text = f"{data.get('what_kind_of_shop', '')} {data.get('last_month', '')}".strip()
        if moves:
            text += " What would help most: " + " ".join(f"{i}. {m['what']}: {m['why']}" for i, m in
                                                         enumerate(moves, 1))
        return text + " The Savings tab shows the 12-month paths. These are estimates from your books."
    if tool == "get_savings":
        text = data.get("headline", "")
        if data.get("scale_up"):
            text += f" {data['scale_up']} Say \"save {data.get('scale_up_to')} every month\" if you want that."
        if data.get("goals"):
            text += " Goals: " + " ".join(g["status"] for g in data["goals"])
        elif data.get("goals_headline"):
            text += f" {data['goals_headline']}"
        return text
    if tool == "set_savings_goal":
        added = data.get("added", {})
        return f"Done: {added.get('status', '')} Savings fill your goals from the top of the list.".strip()
    if tool == "remove_savings_goal":
        return f"Removed the goal {data.get('removed', {}).get('goal')}. {data.get('note', '')}".strip()
    if tool == "add_starter_goals":
        if data.get("nothing_new"):
            return "You already have KhataPilot's starter goals. " + " ".join(g["status"] for g in data.get("goals", []))
        lines = " ".join(f"{i}. {g['goal']}, {g['amount']}{' by ' + g['by'] if g.get('by') else ''}: {g['why']}"
                         for i, g in enumerate(data.get("added", []), 1))
        return (f"I set up {len(data.get('added', []))} goals from your books. {lines} Savings fill them from the "
                "top. Say \"remove the Eid goal\" to drop one, or add your own: \"goal 35,000 for a fridge by March\".")
    if tool == "set_savings_share":
        share = data.get("share_of_profit")
        if not share:
            return "Paused: no savings will be suggested until you set a share again. Nothing else changed."
        was = f" (it was {data['was']})" if data.get("was") and data["was"] != share else ""
        text = f"Done: you'll save {share} of each month's profit{was}. {data.get('headline', '')}".strip()
        if added := data.get("starter_goals_added"):
            names = ", ".join(f"{g['goal']} {g['amount']}" for g in added)
            text += (f" To start you off I set {len(added)} goals from your books: {names}. See them in the Savings "
                     "tab; say \"remove the Eid goal\" to drop one or add your own.")
        return text
    if tool == "list_recent_entries":
        lines = [f"{e['kind'].replace('_', ' ')} {e['amount']} ({e['method']})" for e in data.get("entries", [])[:5]]
        return "Latest entries: " + "; ".join(lines) + "."
    if tool == "list_trusted_payees":
        payees = outcome.data or []
        return "Trusted payees: " + ", ".join(f"{p['name']} ({p['number']}, {p['method']})" for p in payees) + "."
    if tool == "get_limits":
        return f"Your limits: {data.get('per_payment')} per payment and {data.get('per_day')} per day. {data.get('meaning', '')}".strip()
    if tool == "list_blocked_numbers":
        rows = data.get("blocked", [])
        if not rows:
            return "No numbers are blocked yet."
        return "Blocked: " + "; ".join(f"{r['number']} ({r['reason']}, since {r['since']})" for r in rows) + "."
    if tool == "list_reminders":
        rows = data.get("reminders", [])
        if not rows:
            return "No baki reminders have been sent yet."
        who = "; ".join(f"{r['customer']}: {r['outcome'].replace('_', ' ')}, still owes {r['still_owes']}" for r in rows[:5])
        return (f"Since the reminders: {data.get('paid', 0)} paid in full, {data.get('part_paid', 0)} in part, "
                f"{data.get('waiting', 0)} not yet. {who}.")
    if tool == "set_reminder_opt_out":
        if data.get("reminders") == "off":
            already = "were already off" if data.get("already") else "are off"
            return f"Done: reminders to {data.get('customer')} {already}. {data.get('meaning', '')}".strip()
        return f"Done: {data.get('meaning', '')}".strip()
    if tool == "notify_owner":
        return "I told the owner about this message and did nothing else."
    if tool in ("add_bill", "update_bill", "delete_bill") or (tool == "list_bills" and data.get("bills")):
        return describe_bills(tool, data)
    if tool == "list_bills":
        return "There are no open bills to pay."
    return outcome.message


def _bill_line(b: dict) -> str:
    when = (f"overdue since {b['due']}" if b["overdue"] else "due today" if b["days_left"] == 0
            else f"due {b['due']}, in {b['days_left']} day{'s' if b['days_left'] != 1 else ''}")
    return f"{b['payee']} {b['amount_left']} ({when}{', priority' if b['priority'] else ''})"


def describe_bills(tool: str, data: dict) -> str:
    if tool == "add_bill":
        return f"Added the bill: {_bill_line(data['added'])}."
    if tool == "delete_bill":
        return f"Removed the bill: {_bill_line(data['removed'])}. It is kept as cancelled in the books."
    if tool == "update_bill":
        return f"Updated the bill: {_bill_line(data['now'])}. Before: {data['before']['amount_left']} due {data['before']['due']}."
    lines = "; ".join(_bill_line(b) for b in data["bills"][:8])
    return f"{len(data['bills'])} open bills, {data['total_due']} in all: {lines}."


def compose(brief: TurnBrief, calls: list[LLMToolCall], results: list[ToolResult]) -> str:
    if not calls:
        if brief.sender == "owner" and (question := bill_question(brief.owner_text, brief.today)
                                        or baki_change_question(brief.owner_text)
                                        or payment_question(brief.owner_text)):
            return question
        return ("I can record sales, purchases and bKash/Nagad SMS, show today's totals and balances, list who owes "
                "you, warn you before cash runs short, pay suppliers within your limits, and find you a loan. "
                "For example: "
                "\"sold 3 bags of rice ৳1,500 cash\".")
    by_id = {r.call_id: r.outcome for r in results}
    parts = list(dict.fromkeys(describe(c.name, by_id[c.id]) for c in calls if c.id in by_id))
    ledger_today = next((by_id[c.id].data.get("today") for c in reversed(calls)
                         if c.name in ("record_sms", "add_ledger_entry", "record_customer_payment") and c.id in by_id
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
