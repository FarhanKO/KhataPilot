"""The guard's checks. Each is a small pure function of facts the LLM cannot change, returning a
`CheckResult` with a plain-English detail for the decision trace and the audit log."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from app.core.actions import ActionKind, PaymentRequest, ProposedAction
from app.core.clock import to_local
from app.core.decisions import CheckResult, CheckStatus
from app.core.masking import find_phone_numbers, mask_phone
from app.core.money import find_amounts, fmt_taka
from app.core.provenance import Provenance, TurnContext
from app.guard.injection import InjectionReport
from app.guard.intent import IntentMatch
from app.guard.policy_store import KillSwitchView, Limits, TrustedPayeeView, normalize_name
from app.guard.tiers import SIDE_EFFECT_KINDS
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
    if ctx.action.kind is not ActionKind.PAYMENT:
        return _result("pin_lock", label, CheckStatus.SKIP, "Not a money action.")
    if ctx.pin_locked_until:
        until = to_local(ctx.pin_locked_until).strftime("%H:%M")
        return _result("pin_lock", label, CheckStatus.BLOCK, f"Money actions are locked until {until} after 3 wrong PINs.")
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


def intent(ctx: CheckContext) -> CheckResult:
    label = "Intent match"
    if ctx.action.kind is not ActionKind.PAYMENT:
        return _result("intent", label, CheckStatus.SKIP, "Only payments need a matching instruction from you.")
    match = ctx.intent
    if match is None or not match.matched:
        detail = match.detail if match else "You did not ask for this payment."
        return _result("intent", label, CheckStatus.BLOCK, detail)
    if match.ambiguous:
        return _result("intent", label, CheckStatus.ESCALATE,
                       f"{match.detail}, but it mentions more than one amount, so please confirm.", tier=3)
    return _result("intent", label, CheckStatus.PASS, match.detail)


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
        if segment is None or segment.provenance is not Provenance.UNTRUSTED or segment.source != "forwarded_sms":
            return _result("grounded", label, CheckStatus.BLOCK, f"There is no forwarded SMS number {index} in this message.")
        return _result("grounded", label, CheckStatus.PASS,
                       f"Booked straight from forwarded SMS {index}; the model retyped nothing.")
    amount = params.get("amount_paisa")
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


PIPELINE = (provenance, kill_switch, pin_lock, hidden_instructions, intent, grounded, payee, limits, velocity, fraud)
