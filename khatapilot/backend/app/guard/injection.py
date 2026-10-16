"""Prompt-injection detector for text the owner did not write (and a narrower warning signal on text
they did).

This is a *signal*, not the main defence. The main defence is structural: untrusted text can never
start a money action (provenance), payment intents are read only from the owner's own words
(intent), and the provider refuses anything the guard did not sign. The detector exists so the
owner is told, and the attempt is logged, whenever forwarded content tries to give orders.

Before matching, the text is normalised: Unicode NFKC, zero-width and soft-hyphen characters
removed, Cyrillic/Greek look-alike letters mapped to Latin, and common digit-for-letter swaps
undone. Base64-looking chunks are decoded and scanned too.

The owner's own words get `scan_owner`, which looks only for signs that someone else wrote them:
text pretending to be a system or developer message, text shaped like a tool call, or an order to
ignore the assistant's instructions or rules. Money words, urgency and "without asking me" are how
owners talk about their own payments, and "ignore my last message" is how they correct themselves,
so none of those count there (red-team benign cases BEN-18 to BEN-22).
"""

from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from dataclasses import dataclass, field

from app.core.masking import mask_text

ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍‎‏⁠﻿­᠎"), None)
HOMOGLYPHS = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s",
    "ԁ": "d", "ɡ": "g", "һ": "h", "ӏ": "l", "ո": "n", "ս": "u", "ν": "v", "ο": "o", "α": "a", "ε": "e",
    "ι": "i", "κ": "k", "ρ": "p", "τ": "t", "υ": "u", "χ": "x", "Β": "b", "Ε": "e", "Ι": "i", "Κ": "k",
    "Μ": "m", "Ν": "n", "Ο": "o", "Ρ": "p", "Τ": "t", "Χ": "x", "Υ": "y", "А": "a", "В": "b", "Е": "e",
    "К": "k", "М": "m", "Н": "h", "О": "o", "Р": "p", "С": "c", "Т": "t", "Х": "x",
})
LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})
BASE64_CHUNK = re.compile(r"[A-Za-z0-9+/_-]{16,}={0,2}")


@dataclass(frozen=True)
class Rule:
    name: str
    category: str
    pattern: re.Pattern[str]
    weight: float
    explanation: str


def _rule(name: str, category: str, pattern: str, weight: float, explanation: str) -> Rule:
    return Rule(name, category, re.compile(pattern, re.IGNORECASE | re.DOTALL), weight, explanation)


RULES: tuple[Rule, ...] = (
    # --- attempts to override the assistant's instructions
    _rule("override_previous", "override",
          r"\b(ignore|disregard|forget|override|bypass|skip|drop)\b.{0,40}?\b(previous|prior|above|earlier|all|any|your|the|"
          r"system|safety|security|existing)\b.{0,30}?\b(instructions?|rules?|prompts?|messages?|guidelines|limits?|checks?|"
          r"restrictions?|policy|policies)\b",
          0.9, "tells the assistant to ignore its instructions"),
    _rule("new_instructions", "override", r"\b(new|updated|real|actual|secret|hidden)\s+(instructions?|orders?|rules?)\b",
          0.6, "claims to give new instructions"),
    _rule("persona_switch", "override", r"\b(you\s+are\s+now|from\s+now\s+on\s+you|act\s+as|pretend\s+(to\s+be|you\s+are)|roleplay)\b",
          0.6, "tries to change who the assistant is"),
    _rule("fake_system", "override",
          r"((^|[\n.!?]|\s)\s*(system|assistant|developer)\s*:|<\|?\s*(system|im_start|assistant)\s*\|?>|\[(system|inst)\]|"
          r"\b(system|developer|admin)\s+(prompt|message|override|mode|note|notice)\b|\bdeveloper\s+mode\b|\bjailbreak\b)",
          0.8, "pretends to be a system or developer message"),
    _rule("no_owner", "override",
          r"(\b(do\s*n[o']?t|never)\b.{0,10}\b(tell|inform|alert|notify|ask|wake|bother|show)\b.{0,15}\b(the\s+)?(owner|him|her|"
          r"them|boss|malik|user|anyone|shopkeeper)\b|\bwithout\b.{0,15}\b(asking|telling|informing|notifying|confirmation|"
          r"confirming|approval|approving|pin|otp|checking|verification|verifying)\b)",
          0.6, "asks to act without telling or asking the owner"),
    # --- money instructions hidden in content
    _rule("money_instruction", "money",
          r"\b(send|transfer|pay|remit|cash\s*out|bkash|nagad|pathao|pathan|pathiye\s+dao)\b[^\n]{0,50}?"
          r"(\d[\d,]*\s*(tk|taka|bdt|/-)?|৳\s*\d)",
          0.5, "contains an instruction to send money"),
    _rule("changed_account", "money",
          r"\b(new|changed|updated|different|correct)\s+(bkash|nagad|bank|wallet|account|number)\b",
          0.5, "claims a payee's account or number has changed"),
    # --- pressure and false authority
    _rule("urgency", "pressure", r"\b(urgent(ly)?|immediately|right\s+now|asap|within\s+\d+\s*(min|mins|minutes|hours?)|last\s+chance|final\s+warning)\b",
          0.3, "creates false urgency"),
    _rule("fake_authority", "pressure",
          r"\b(bkash|nagad|bank|bangladesh\s+bank|police|rab|nbr|tax|khata\s*pilot)\s*(support|customer\s*care|helpline|agent|officer|team|"
          r"authority|department|security)\b",
          0.4, "claims to speak for a provider or authority"),
    _rule("account_threat", "pressure",
          r"\b(account|wallet|shop|license|licence)\b.{0,20}\b(will\s+be|has\s+been|is\s+being)\s+(blocked|suspended|closed|locked|frozen|cancelled)\b",
          0.5, "threatens to block an account"),
    # --- fishing for secrets
    _rule("secret_fishing", "secrets",
          r"(\b(pin|otp|password|passcode|secret\s+code|verification\s+code)\b.{0,30}\b(share|send|tell|give|reveal|type|enter|dao|den|"
          r"bolo|bolen|pathao)\b|\b(share|send|tell|give|reveal|reply\s+with|what\s+is)\b.{0,30}\b(pin|otp|password|passcode)\b)",
          0.8, "asks for the PIN, an OTP or a password"),
    # --- data exfiltration
    _rule("exfiltration", "exfiltration",
          r"\b(send|share|export|forward|list|give|dump)\b.{0,30}\b(all|every|entire|whole|full)\b.{0,25}\b(customers?|numbers|contacts|"
          r"data|ledger|records|books|transactions|accounts?)\b",
          0.7, "asks for the shop's data in bulk"),
    # --- tool-call smuggling
    _rule("tool_injection", "tooling",
          r"(\"?(tool|function)_?(call|name)\"?\s*[:=]|\b(propose_payment|schedule_payment|send_reminders|submit_application|"
          r"add_ledger_entry)\s*\(|\{\s*\"(amount|payee|to_number|amount_paisa)\"\s*:)",
          0.8, "contains text shaped like a tool call"),
    # --- Banglish
    _rule("banglish_override", "override",
          r"\bager\s+(shob|sob|sokol|sb)?\s*(nirdesh|nirdeshona|instruction|kotha)\b.{0,30}\b(bad|baad|bhule|vule|ignore|batil)\b",
          0.9, "tells the assistant to ignore its instructions (Banglish)"),
    _rule("banglish_money", "money",
          r"\b(ekhoni|ekhuni|ekhon\s+i|taratari|jaldi)\b.{0,30}\b(\d[\d,]*\s*)?(taka|tk)\b.{0,25}\b(pathao|pathan|dao|den|send)\b",
          0.6, "urgently asks for money to be sent (Banglish)"),
    _rule("banglish_secret", "secrets", r"\b(pin|otp|password)\s*(ta|number|code)?\s*(dao|den|bolo|bolen|pathao|share\s+koro)\b",
          0.8, "asks for the PIN or OTP (Banglish)"),
    # --- Bangla script
    _rule("bangla_override", "override", r"আগের\s*(সব|সকল)?\s*(নির্দেশ|নির্দেশনা|কথা).{0,20}(ভুলে|উপেক্ষা|বাদ)",
          0.9, "tells the assistant to ignore its instructions (Bangla)"),
    _rule("bangla_money", "money", r"(টাকা|৳).{0,25}(পাঠাও|পাঠান|পাঠিয়ে\s*দাও|দাও|দিন|ট্রান্সফার)",
          0.5, "contains an instruction to send money (Bangla)"),
    _rule("bangla_secret", "secrets",
          r"(পিন|ওটিপি|পাসওয়ার্ড|গোপন\s*নম্বর).{0,20}(দিন|দাও|বলুন|বলো|পাঠান|পাঠাও|শেয়ার\s*কর)",
          0.8, "asks for the PIN, OTP or password (Bangla)"),
    _rule("bangla_urgency", "pressure", r"(এখনই|জরুরি|তাড়াতাড়ি|দ্রুত)", 0.3, "creates urgency (Bangla)"),
)

# For the owner's own words: only signs that someone else wrote the text (see the module docstring).
OWNER_RULES: tuple[Rule, ...] = (
    _rule("override_instructions", "override",
          r"\b(ignore|disregard|forget|override|bypass|skip|drop)\b.{0,40}?\b(previous|prior|above|earlier|all|any|your|the|"
          r"system|safety|security|existing)\b.{0,30}?\b(instructions?|rules?|prompts?|guidelines|checks?|restrictions?|"
          r"policy|policies|guard)\b",
          0.9, "tells the assistant to ignore its instructions"),
    _rule("banglish_override_instructions", "override",
          r"\bager\s+(shob|sob|sokol|sb)?\s*(nirdesh|nirdeshona|instructions?|rules?)\b.{0,30}\b(bad|baad|bhule|vule|ignore|batil)\b",
          0.9, "tells the assistant to ignore its instructions (Banglish)"),
    _rule("bangla_override_instructions", "override", r"আগের\s*(সব|সকল)?\s*(নির্দেশ|নির্দেশনা).{0,20}(ভুলে|উপেক্ষা|বাদ)",
          0.9, "tells the assistant to ignore its instructions (Bangla)"),
    *(rule for rule in RULES if rule.name in {"fake_system", "tool_injection"}),
)

SUSPECT_THRESHOLD = 0.6
# Safety advice such as "Never share your PIN" (common in real bKash SMS) is not an attack.
NEGATABLE = {"secret_fishing", "banglish_secret"}
NEGATION = re.compile(r"\b(never|don'?t|do\s+not|kokhono|kokhonoi|kakeo\s+na)\b[^.!?]{0,15}$", re.IGNORECASE)


@dataclass(frozen=True)
class Finding:
    rule: str
    category: str
    explanation: str
    excerpt: str


@dataclass(frozen=True)
class InjectionReport:
    suspected: bool
    score: float
    findings: tuple[Finding, ...] = field(default_factory=tuple)
    decoded_payload: bool = False

    @property
    def categories(self) -> set[str]:
        return {f.category for f in self.findings}

    def summary(self) -> str:
        if not self.findings:
            return "No instructions found in this text."
        reasons = sorted({f.explanation for f in self.findings})
        return "Suspected injection: " + "; ".join(reasons) + "."


CLEAN = InjectionReport(suspected=False, score=0.0)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(ZERO_WIDTH).translate(HOMOGLYPHS)
    return re.sub(r"\s+", " ", text).strip()


def _decoded_chunks(text: str) -> list[str]:
    decoded = []
    for chunk in BASE64_CHUNK.findall(text):
        try:
            raw = base64.b64decode(chunk + "=" * (-len(chunk) % 4), altchars=b"-_" if ("-" in chunk or "_" in chunk) else None)
        except (binascii.Error, ValueError):
            continue
        try:
            candidate = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        printable = sum(ch.isprintable() or ch.isspace() for ch in candidate)
        if candidate and printable / len(candidate) > 0.9 and re.search(r"[A-Za-zঀ-৿]{3}", candidate):
            decoded.append(candidate)
    return decoded


def _excerpt(text: str, match: re.Match[str]) -> str:
    start, end = max(0, match.start() - 20), min(len(text), match.end() + 20)
    return mask_text(text[start:end]).strip()


def scan(text: str, rules: tuple[Rule, ...] = RULES) -> InjectionReport:
    if not text or not text.strip():
        return CLEAN
    base = normalize(text)
    variants = [base, base.translate(LEET)]
    decoded = _decoded_chunks(text)
    variants += [normalize(d) for d in decoded]
    findings: dict[str, Finding] = {}
    for variant in variants:
        for rule in rules:
            if rule.name in findings:
                continue
            for match in rule.pattern.finditer(variant):
                if rule.name in NEGATABLE and NEGATION.search(variant[max(0, match.start() - 20):match.start()]):
                    continue
                findings[rule.name] = Finding(rule.name, rule.category, rule.explanation, _excerpt(variant, match))
                break
    weight = {rule.name: rule.weight for rule in rules}
    score = min(1.0, sum(weight[name] for name in findings))
    strong = any(weight[name] >= 0.8 for name in findings)
    return InjectionReport(
        suspected=strong or score >= SUSPECT_THRESHOLD,
        score=round(score, 2),
        findings=tuple(findings.values()),
        decoded_payload=bool(decoded) and any(
            rule.pattern.search(normalize(d)) for d in decoded for rule in rules),
    )


def scan_owner(text: str) -> InjectionReport:
    """The owner's own words: flagged only when they read as if someone else wrote them."""
    return scan(text, OWNER_RULES)
