"""The guard's checks. Each is a small pure function of facts the LLM cannot change, returning a
`CheckResult` with a plain-English detail for the decision trace and the audit log."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from app.core.actions import ActionKind, PaymentRequest, ProposedAction
from app.core.clock import to_local
from app.core.decisions import CheckResult, CheckStatus
from app.core.masking import find_phone_numbers, mask_number, mask_phone, normalize_msisdn
from app.core.money import find_amounts, fmt_taka, normalize_digits
from app.core.provenance import Provenance, TurnContext
from app.guard.injection import InjectionReport
from app.guard.intent import IntentMatch
from app.guard.policy_store import KillSwitchView, Limits, TrustedPayeeView, normalize_name
from app.guard.tiers import CONTROL_KINDS, SIDE_EFFECT_KINDS
from app.modules.risk.schemas import RiskScore


@dataclass(frozen=True)
class CheckContext:
    turn: TurnContext
    action: ProposedAction
    today: date
    limits: Limits
    kill_switch: KillSwitchView
    pin_locked_until: datetime | None
    untrusted_scan: InjectionReport
    owner_scan: InjectionReport
    intent: IntentMatch | None
    trusted_by_number: TrustedPayeeView | None
    trusted_by_name: TrustedPayeeView | None
    blocked_reason: str | None
    committed_today_paisa: int
    recent_same_payee_paisa: tuple[int, ...]
    recent_new_payees_paisa: tuple[int, ...]
    payments_last_hour: int
    risk: RiskScore | None

    @property
    def payment(self) -> PaymentRequest | None:
        return self.action.payment

    @property
    def payee_label(self) -> str:
        p = self.payment
        if p is None:
            return "the payee"
        if p.payee_name:
            return p.payee_name
        return mask_phone(p.payee_number) if p.payee_number else "the payee"


PERCENT_RE = re.compile(r"(?<![\d.])(\d{1,2})\s*(?:%|percent\b|per\s*cent\b|pc\b|shotangsho|shotangsh|শতাংশ)", re.I)


def named_by_owner(name: str, owner_text: str) -> bool:
    """The owner's own words name this payee: a word of the name (3+ letters) starts a word they typed,
    so "Meghnar bill" names Meghna Distributors."""
    said = f" {normalize_name(owner_text)} "
    return any(f" {w}" in said for w in normalize_name(name).split() if len(w) >= 3)


def _result(name: str, label: str, status: CheckStatus, detail: str, tier: int | None = None) -> CheckResult:
    return CheckResult(name=name, label=label, status=status, detail=detail, tier=tier)


def provenance(ctx: CheckContext) -> CheckResult:
    label = "Provenance"
    if ctx.action.kind is ActionKind.NOTIFY_OWNER:
        return _result("provenance", label, CheckStatus.PASS, "Telling the owner is always allowed.")
    if ctx.turn.is_owner_turn:
        return _result("provenance", label, CheckStatus.PASS, f"Started by you, the authenticated owner ({ctx.turn.channel}).")
    return _result(
        "provenance", label, CheckStatus.BLOCK,
        f"This came from {ctx.turn.sender.display}, not from you. Only your own messages can make KhataPilot "
        "act, read the shop's data or touch your books.",
    )


def kill_switch(ctx: CheckContext) -> CheckResult:
    label = "Kill switch"
    if not ctx.kill_switch.frozen:
        return _result("kill_switch", label, CheckStatus.PASS, "STOP is off.")
    since = to_local(ctx.kill_switch.since).strftime("%H:%M") if ctx.kill_switch.since else "earlier"
    if ctx.action.kind in SIDE_EFFECT_KINDS:
        return _result("kill_switch", label, CheckStatus.BLOCK,
                       f"STOP is on (since {since}): every payment and outgoing message is frozen until you resume with your PIN.")
    return _result("kill_switch", label, CheckStatus.INFO, f"STOP is on (since {since}); reading your own books still works.")


def pin_lock(ctx: CheckContext) -> CheckResult:
    label = "PIN lock"
    if ctx.action.kind not in (ActionKind.PAYMENT, ActionKind.CONTROL_CHANGE):
        return _result("pin_lock", label, CheckStatus.SKIP, "Not a money action.")
    if ctx.pin_locked_until:
        until = to_local(ctx.pin_locked_until).strftime("%H:%M")
        what = "Money actions" if ctx.action.kind is ActionKind.PAYMENT else "Changes to your limits and payees"
        return _result("pin_lock", label, CheckStatus.BLOCK, f"{what} are locked until {until} after 3 wrong PINs.")
    return _result("pin_lock", label, CheckStatus.PASS, "Not locked.")


def hidden_instructions(ctx: CheckContext) -> CheckResult:
    """Forwarded content that gives orders, and payment values that come only from such content."""
    label = "Hidden instructions"
    p = ctx.payment
    if ctx.action.kind in (ActionKind.PAYMENT, ActionKind.MESSAGE) and p is not None:
        untrusted_numbers = set(find_phone_numbers(ctx.turn.untrusted_text))
        owner_numbers = set(find_phone_numbers(ctx.turn.owner_text))
        trusted_number = ctx.trusted_by_number is not None
        if p.payee_number and p.payee_number in untrusted_numbers and p.payee_number not in owner_numbers and not trusted_number:
            return _result("hidden_instructions", label, CheckStatus.BLOCK,
                           f"The payee number {mask_phone(p.payee_number)} comes from a forwarded message or invoice, "
                           "not from you.")
    if ctx.untrusted_scan.suspected:
        if ctx.action.kind in SIDE_EFFECT_KINDS:
            return _result("hidden_instructions", label, CheckStatus.ESCALATE,
                           f"Forwarded content in this conversation tried to give orders ({ctx.untrusted_scan.summary()}). "
                           "You must confirm with your PIN.", tier=3)
        return _result("hidden_instructions", label, CheckStatus.INFO, ctx.untrusted_scan.summary())
    if ctx.owner_scan.suspected and ctx.action.kind in SIDE_EFFECT_KINDS:
        return _result("hidden_instructions", label, CheckStatus.ESCALATE,
                       "Your message reads like an instruction override, so this needs your PIN.", tier=3)
    return _result("hidden_instructions", label, CheckStatus.PASS, "No hidden instructions found.")


SAVINGS_TOOL = "save_to_savings"


def savings_facts(ctx: CheckContext) -> dict | None:
    """The savings plan's facts, put on the proposal by the guard's own savings tool (never by the model),
    or None when this is not a savings transfer."""
    if ctx.action.tool != SAVINGS_TOOL:
        return None
    facts = ctx.action.params.get("savings")
    return facts if isinstance(facts, dict) else {}


def intent(ctx: CheckContext) -> CheckResult:
    label = "Intent match"
    if ctx.action.kind is not ActionKind.PAYMENT:
        return _result("intent", label, CheckStatus.SKIP, "Only payments need a matching instruction from you.")
    if (facts := savings_facts(ctx)) is not None:
        p = ctx.payment
        if p is None or not facts or p.purpose != "savings":
            return _result("intent", label, CheckStatus.BLOCK, "You did not ask for this transfer.")
        if facts.get("source") == "plan":
            return _result("intent", label, CheckStatus.PASS,
                           f"{fmt_taka(p.amount_paisa)} comes from your savings plan ({facts.get('share_pct')}% of "
                           f"{facts.get('based_on')}'s profit, capped by the cash forecast), worked out from your "
                           "books, not by the model.")
        if p.amount_paisa in find_amounts(ctx.turn.owner_text):
            return _result("intent", label, CheckStatus.PASS, f"{fmt_taka(p.amount_paisa)} appears in your own message.")
        return _result("intent", label, CheckStatus.BLOCK,
                       f"{fmt_taka(p.amount_paisa)} is not in your message, so nothing was moved.")
    match = ctx.intent
    if match is None or not match.matched:
        detail = match.detail if match else "You did not ask for this payment."
        return _result("intent", label, CheckStatus.BLOCK, detail)
    if match.ambiguous:
        return _result("intent", label, CheckStatus.ESCALATE,
                       f"{match.detail}, but it mentions more than one amount, so please confirm.", tier=3)
    return _result("intent", label, CheckStatus.PASS, match.detail)


# Where an SMS to book can come from: forwarded in chat, or from the owner's phone (labelled by the phone channel
# only, `phone_sms_checked` once the owner has looked at it in the app and tapped Book).
SMS_SOURCES = frozenset({"forwarded_sms", "phone_sms", "phone_sms_checked"})


def grounded(ctx: CheckContext) -> CheckResult:
    """Bookkeeping entries must come from the owner's own words or from a forwarded SMS, never from
    a number the model made up or read in someone else's message."""
    label = "Grounding"
    if ctx.action.kind is not ActionKind.LEDGER_WRITE:
        return _result("grounded", label, CheckStatus.SKIP, "Only bookkeeping entries need grounding.")
    params = ctx.action.params
    if ctx.action.tool == "record_sms":
        index = params.get("segment")
        segments = ctx.turn.segments
        valid = isinstance(index, int) and 1 <= index <= len(segments)
        segment = segments[index - 1] if valid else None
        if segment is None or segment.provenance is not Provenance.UNTRUSTED or segment.source not in SMS_SOURCES:
            return _result("grounded", label, CheckStatus.BLOCK, f"There is no forwarded SMS number {index} in this message.")
        if segment.source != "forwarded_sms":
            return _result("grounded", label, CheckStatus.PASS,
                           "Booked straight from the SMS your phone received; nothing was retyped.")
        return _result("grounded", label, CheckStatus.PASS,
                       f"Booked straight from forwarded SMS {index}; the model retyped nothing.")
    if ctx.action.tool in ("set_savings_share", "set_reinvest_share"):
        what = "your savings share" if ctx.action.tool == "set_savings_share" else "the share put back into stock"
        if params.get("pause"):
            return _result("grounded", label, CheckStatus.PASS, "Pausing your savings moves no money.")
        if params.get("automatic"):
            return _result("grounded", label, CheckStatus.PASS,
                           "KhataPilot sizes it from your books again; no number from anyone's message is used.")
        share = params.get("share_pct")
        if share is None:  # only the basis: last month or the last three months, no number involved
            return _result("grounded", label, CheckStatus.PASS,
                           "Choosing what your savings are a share of moves no money.")
        said = {int(m) for m in PERCENT_RE.findall(normalize_digits(ctx.turn.owner_text))}
        if share in said:
            return _result("grounded", label, CheckStatus.PASS, f"{share}% appears in your own message.")
        return _result("grounded", label, CheckStatus.BLOCK,
                       f"{share}% is not in your message, so {what} was not changed.")
    if ctx.action.tool == "add_starter_goals" or (ctx.action.tool == "set_savings_goal" and params.get("starter")):
        return _result("grounded", label, CheckStatus.PASS,
                       "The goal's amount is worked out from your books by KhataPilot's code, not by the model.")
    if ctx.action.tool == "remove_savings_goal":
        name = str(params.get("name") or "")
        if named_by_owner(name, ctx.turn.owner_text):
            return _result("grounded", label, CheckStatus.PASS, f"You named the {name} goal in your own message.")
        return _result("grounded", label, CheckStatus.BLOCK,
                       f"You did not name the {name} goal in your message, so it was not removed.")
    if ctx.action.tool == "set_reminder_opt_out":
        name = str(params.get("customer_name") or "")
        if named_by_owner(name, ctx.turn.owner_text):
            return _result("grounded", label, CheckStatus.PASS, f"You named {name} in your own message.")
        return _result("grounded", label, CheckStatus.BLOCK,
                       f"You did not name {name} in your message, so their reminders were not changed.")
    amount = params.get("amount_paisa")
    if ctx.action.tool == "set_safety_buffer" and params.get("automatic"):
        return _result("grounded", label, CheckStatus.PASS,
                       "Going back to the automatic buffer uses no amount from anyone's message.")
    if ctx.action.tool == "set_safety_buffer" and amount == 0 and \
            re.search(r"(?<![\d,.])0(?![\d,.])|\b(?:zero|no buffer)\b", normalize_digits(ctx.turn.owner_text), re.I):
        return _result("grounded", label, CheckStatus.PASS, "You asked for no safety buffer in your own message.")
    if ctx.action.tool == "correct_balance" and amount == 0 and \
            re.search(r"(?<![\d,.])0(?![\d,.])|\b(?:zero|nothing|empty|kichu nai|kichui nai)\b",
                      normalize_digits(ctx.turn.owner_text), re.I):
        return _result("grounded", label, CheckStatus.PASS, "You said in your own message that nothing is there.")
    if amount is None and isinstance(params.get("payee_name"), str):
        # A bill change without an amount (priority, a new day, removing it): the owner must have named the
        # payee themselves, so a forwarded invoice cannot reprioritise or delete the shop's bills.
        name = params["payee_name"]
        if named_by_owner(name, ctx.turn.owner_text):
            return _result("grounded", label, CheckStatus.PASS, f"You named {name} in your own message.")
        return _result("grounded", label, CheckStatus.BLOCK,
                       f"You did not name {name} in your message, so nothing was changed.")
    if isinstance(amount, int) and amount in find_amounts(ctx.turn.owner_text):
        return _result("grounded", label, CheckStatus.PASS, f"{fmt_taka(amount)} appears in your own message.")
    shown = fmt_taka(amount) if isinstance(amount, int) else "That amount"
    return _result("grounded", label, CheckStatus.BLOCK,
                   f"{shown} is not in your message, so nothing was recorded. KhataPilot only books amounts you typed "
                   "or forwarded.")


def payee(ctx: CheckContext) -> CheckResult:
    label = "Payee"
    p = ctx.payment
    if ctx.action.kind is not ActionKind.PAYMENT or p is None:
        return _result("payee", label, CheckStatus.SKIP, "Not a payment.")
    if ctx.blocked_reason:
        return _result("payee", label, CheckStatus.BLOCK,
                       f"{mask_phone(p.payee_number or '')} is on your blocked list ({ctx.blocked_reason}).")
    if (facts := savings_facts(ctx)) is not None:
        if facts and p.payee_number and p.payee_number == facts.get("account_number"):
            return _result("payee", label, CheckStatus.PASS,
                           f"Your own savings account ({mask_number(p.payee_number)}), saved in the app with your PIN.")
        return _result("payee", label, CheckStatus.BLOCK, "Savings only go to the account you saved with your PIN.")
    by_number, by_name = ctx.trusted_by_number, ctx.trusted_by_name
    if by_number and (not p.payee_name or by_name is None or by_name.id == by_number.id):
        return _result("payee", label, CheckStatus.PASS, f"{by_number.name} is a trusted payee ({by_number.number_masked}).")
    if by_number and by_name and by_name.id != by_number.id:
        return _result("payee", label, CheckStatus.ESCALATE,
                       f"That number belongs to {by_number.name}, not {by_name.name}. Check before confirming.", tier=3)
    if by_name and p.payee_number is None:
        return _result("payee", label, CheckStatus.PASS,
                       f"{by_name.name} is a trusted payee; paying the saved number {by_name.number_masked}.")
    if by_name and p.payee_number:
        return _result("payee", label, CheckStatus.ESCALATE,
                       f"Possible spoof: {by_name.name}'s saved number ends {by_name.number[-4:]}, but this payment "
                       f"goes to a different number ending {p.payee_number[-4:]}.", tier=3)
    who = p.payee_name or mask_phone(p.payee_number or "")
    if p.payee_name and normalize_name(p.payee_name) and p.payee_number is None:
        return _result("payee", label, CheckStatus.BLOCK,
                       f"{who} is not a trusted payee and no number was given, so there is nobody to pay.")
    return _result("payee", label, CheckStatus.ESCALATE,
                   f"New payee: {who} is not on your trusted list. If someone asked you to pay a fee to receive "
                   "money, claim a prize or unlock an account, it is a scam.", tier=3)


def limits(ctx: CheckContext) -> CheckResult:
    label = "Limits"
    p = ctx.payment
    if ctx.action.kind is not ActionKind.PAYMENT or p is None:
        return _result("limits", label, CheckStatus.SKIP, "Not a payment.")
    per_txn, daily = ctx.limits.per_txn_paisa, ctx.limits.daily_paisa
    after = ctx.committed_today_paisa + p.amount_paisa
    if p.amount_paisa > per_txn:
        return _result("limits", label, CheckStatus.ESCALATE,
                       f"{fmt_taka(p.amount_paisa)} is over your {fmt_taka(per_txn)} per-payment limit.", tier=3)
    if after > daily:
        return _result("limits", label, CheckStatus.ESCALATE,
                       f"This would take today's payments to {fmt_taka(after)}, over your {fmt_taka(daily)} daily cap.",
                       tier=3)
    return _result("limits", label, CheckStatus.PASS,
                   f"{fmt_taka(p.amount_paisa)} is within {fmt_taka(per_txn)} per payment; "
                   f"today {fmt_taka(after)} of {fmt_taka(daily)}.")


def velocity(ctx: CheckContext) -> CheckResult:
    label = "Velocity and splitting"
    p = ctx.payment
    if ctx.action.kind is not ActionKind.PAYMENT or p is None:
        return _result("velocity", label, CheckStatus.SKIP, "Not a payment.")
    per_txn, window = ctx.limits.per_txn_paisa, ctx.limits.split_window_hours
    same = sum(ctx.recent_same_payee_paisa)
    if ctx.recent_same_payee_paisa and same + p.amount_paisa > per_txn:
        parts = " + ".join(fmt_taka(a) for a in ctx.recent_same_payee_paisa)
        return _result("velocity", label, CheckStatus.ESCALATE,
                       f"Possible split payment: {parts} already went to {ctx.payee_label} in the last {window} h; "
                       f"with this {fmt_taka(p.amount_paisa)} it is {fmt_taka(same + p.amount_paisa)}, over the "
                       f"{fmt_taka(per_txn)} limit.", tier=3)
    is_new = ctx.trusted_by_number is None and ctx.trusted_by_name is None
    new_total = sum(ctx.recent_new_payees_paisa)
    if is_new and ctx.recent_new_payees_paisa and new_total + p.amount_paisa > per_txn:
        return _result("velocity", label, CheckStatus.ESCALATE,
                       f"Payments to new payees in the last {window} h add up to {fmt_taka(new_total + p.amount_paisa)}, "
                       f"over the {fmt_taka(per_txn)} limit.", tier=3)
    if ctx.payments_last_hour + 1 > ctx.limits.velocity_max_per_hour:
        return _result("velocity", label, CheckStatus.ESCALATE,
                       f"{ctx.payments_last_hour + 1} payments within an hour is unusual for this shop.", tier=3)
    return _result("velocity", label, CheckStatus.PASS, "No splitting or unusual pace.")


def fraud(ctx: CheckContext) -> CheckResult:
    label = "Fraud score"
    if ctx.action.kind is not ActionKind.PAYMENT or ctx.risk is None:
        return _result("fraud", label, CheckStatus.SKIP, "Not a payment.")
    score, reasons = ctx.risk.score, "; ".join(ctx.risk.reasons)
    if score >= ctx.limits.fraud_high:
        return _result("fraud", label, CheckStatus.BLOCK, f"{score:.2f} (high, {ctx.risk.model}): {reasons}.")
    if score >= ctx.limits.fraud_medium:
        return _result("fraud", label, CheckStatus.ESCALATE, f"{score:.2f} (medium, {ctx.risk.model}): {reasons}.", tier=3)
    return _result("fraud", label, CheckStatus.PASS, f"{score:.2f} (low, {ctx.risk.model}): {reasons}.")


def savings(ctx: CheckContext) -> CheckResult:
    """Money leaving the shop for the owner's savings always waits for the PIN, and says so when it is more
    than the forecast says is safe."""
    label = "Savings"
    facts = savings_facts(ctx)
    p = ctx.payment
    if facts is None or p is None:
        return _result("savings", label, CheckStatus.SKIP, "Not a savings transfer.")
    detail = "Moving money out of the shop to your savings always waits for your PIN."
    safe, buffer = facts.get("safe_now_paisa"), facts.get("buffer_paisa")
    if isinstance(safe, int) and isinstance(buffer, int) and p.amount_paisa > safe:
        detail += (f" This is {fmt_taka(p.amount_paisa - safe)} more than the forecast says is safe today: cash could "
                   f"drop below your {fmt_taka(buffer)} safety buffer.")
    return _result("savings", label, CheckStatus.ESCALATE, detail, tier=3)


def _number_in(number: str, text: str) -> bool:
    if not number or not text:
        return False
    if normalize_msisdn(number):
        return number in find_phone_numbers(text)
    return number in re.sub(r"\D", "", normalize_digits(text))


def owner_settings(ctx: CheckContext) -> CheckResult:
    """Changes to limits, trusted payees and the block list asked for in chat. Every figure, number and
    name must come from the owner's own words in this turn, and the owner always confirms in the app."""
    label = "Your settings"
    kind, params = ctx.action.kind, ctx.action.params
    if kind not in CONTROL_KINDS:
        return _result("owner_settings", label, CheckStatus.SKIP, "Not a change to your settings.")
    owner = ctx.turn.owner_text
    what = params.get("control")
    if kind is ActionKind.CONTROL_CHANGE and ctx.untrusted_scan.suspected:
        return _result("owner_settings", label, CheckStatus.BLOCK,
                       "Forwarded content in this message tried to give orders, so KhataPilot will not change your "
                       "limits or trusted payees from it. Use the Controls tab if you want the change.")
    if what == "limits":
        said = find_amounts(owner)
        changes = [(label_, params.get(f"{key}_paisa"), params.get(f"{key}_before_paisa"))
                   for label_, key in (("per payment", "per_txn"), ("per day", "daily"))]
        changes = [c for c in changes if c[1] is not None]
        missing = [fmt_taka(new) for _, new, _ in changes if new not in said]
        if missing:
            return _result("owner_settings", label, CheckStatus.BLOCK,
                           f"{' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} not in your message, so "
                           "the limits were not changed.")
        raised = [(label_, new, old) for label_, new, old in changes if old is None or new > old]
        if raised:
            steps = "; ".join(f"{label_} {fmt_taka(old)} to {fmt_taka(new)}" if old is not None
                              else f"{label_} {fmt_taka(new)}" for label_, new, old in raised)
            per_payment = next((new for label_, new, _ in raised if label_ == "per payment"), None)
            tail = (f". A trusted payee could then be paid up to {fmt_taka(per_payment)} at once without asking you."
                    if per_payment else ".")
            return _result("owner_settings", label, CheckStatus.ESCALATE,
                           f"Higher limits mean more can move without your PIN: {steps}{tail}", tier=3)
        return _result("owner_settings", label, CheckStatus.PASS,
                       "Lower limits: more payments will ask for your PIN. The amounts are in your own message.")
    if what == "add_payee":
        number, name = params.get("number", ""), params.get("name", "")
        if not _number_in(number, owner):
            return _result("owner_settings", label, CheckStatus.BLOCK,
                           f"{mask_number(number)} is not in your own message. KhataPilot only trusts a number you "
                           "typed yourself, never one from a forwarded message or invoice.")
        if not named_by_owner(name, owner):
            return _result("owner_settings", label, CheckStatus.BLOCK,
                           f"You did not name {name} in your message, so nobody was added.")
        return _result("owner_settings", label, CheckStatus.ESCALATE,
                       f"Payments to {name} within your limits will go without your PIN. Only trust a number you "
                       "have checked yourself: a message asking you to change a supplier's number is a common scam.",
                       tier=3)
    if what == "savings_account":
        number = params.get("number", "")
        if not _number_in(number, owner):
            return _result("owner_settings", label, CheckStatus.BLOCK,
                           f"{mask_number(number)} is not in your own message. Your savings only go to a number you "
                           "typed yourself, never one from a forwarded message.")
        if ctx.blocked_reason:
            return _result("owner_settings", label, CheckStatus.BLOCK, f"{mask_number(number)} is on your blocked list.")
        return _result("owner_settings", label, CheckStatus.ESCALATE,
                       f"Each month's savings will be sent to {mask_number(number)}. Make sure it is your own account: "
                       "someone asking you to save into their number is a scam.", tier=3)
    if what == "remove_payee":
        name = params.get("name", "")
        if not named_by_owner(name, owner):
            return _result("owner_settings", label, CheckStatus.BLOCK,
                           f"You did not name {name} in your message, so nobody was removed.")
        return _result("owner_settings", label, CheckStatus.PASS, f"You named {name} in your own message.")
    if what == "block":
        number = params.get("number", "")
        if ctx.blocked_reason:
            return _result("owner_settings", label, CheckStatus.BLOCK,
                           f"{mask_number(number)} is already on your blocked list.")
        if not (_number_in(number, owner) or _number_in(number, ctx.turn.untrusted_text)):
            return _result("owner_settings", label, CheckStatus.BLOCK,
                           f"{mask_number(number)} is not in your message or anything you forwarded, so nothing was "
                           "blocked.")
        if ctx.trusted_by_number:
            return _result("owner_settings", label, CheckStatus.ESCALATE,
                           f"This is {ctx.trusted_by_number.name}'s number, one of your trusted payees: blocking it "
                           "stops every payment to them.", tier=1)
        return _result("owner_settings", label, CheckStatus.PASS,
                       "Blocking only makes the shop safer; you still tap Approve.")
    return _result("owner_settings", label, CheckStatus.BLOCK, "Unknown settings change.")


PIPELINE = (provenance, kill_switch, pin_lock, hidden_instructions, intent, grounded, payee, limits, velocity, fraud,
            savings, owner_settings)
