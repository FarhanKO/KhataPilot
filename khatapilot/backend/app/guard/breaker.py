"""The safety breaker: STOP turns itself on when someone keeps trying.

One blocked attempt is the guard doing its job. Several in a short time mean someone is pushing: a
scammer trying number after number, or a fooled model proposing payment after payment. After
`blocks` blocked attempts to move money, message someone, apply for a loan or change a safety
setting within `minutes`, the breaker pulls STOP exactly as the owner's button does: every payment
and outgoing message is frozen, scheduled payments are paused, the provider freezes the accounts,
and the owner is alerted. Only the owner's PIN turns it off again, and the count starts afresh after
every STOP or resume, so the attempts that tripped it never trip it twice.

The count comes from the hash-chained audit log, so it survives restarts and cannot be reset by
anything the model says. A blocked read (someone else asking for the books) does not count: it moves
nothing. The trade-off: a stranger who sends three scam messages can freeze payments until the owner
enters the PIN. That fails safe, and the alert says why.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.core.actions import ActionKind
from app.core.clock import Clock
from app.core.decisions import Decision, Outcome
from app.core.provenance import TurnContext
from app.guard.killswitch import KillSwitchService
from app.guard.recorder import Recorder
from app.infra.audit_log import AuditLog

COUNTED_KINDS = {ActionKind.PAYMENT.value, ActionKind.MESSAGE.value, ActionKind.LOAN_SUBMIT.value,
                 ActionKind.CONTROL_CHANGE.value, ActionKind.CONTROL_BLOCK.value}


def counts(entry_action: dict | None) -> bool:
    return bool(entry_action) and entry_action.get("kind") in COUNTED_KINDS


class SafetyBreaker:
    def __init__(self, *, clock: Clock, audit: AuditLog, kill_switch: KillSwitchService, recorder: Recorder,
                 blocks: int, minutes: int) -> None:
        self.clock = clock
        self.audit = audit
        self.kill_switch = kill_switch
        self.recorder = recorder
        self.blocks = blocks
        self.window = timedelta(minutes=minutes)

    @property
    def enabled(self) -> bool:
        return self.blocks > 0 and self.window > timedelta(0)

    def recent_blocks(self) -> int:
        """Blocked attempts that count, inside the window and since the last STOP or resume."""
        since = self.clock.now() - self.window
        last_switch = next(iter(self.audit.entries(event="kill_switch", limit=1, newest_first=True)), None)
        after = last_switch.seq if last_switch else 0
        n = 0
        for entry in self.audit.entries(event="decision", decision="BLOCKED", since_seq=after, limit=200,
                                        newest_first=True):
            if datetime.fromisoformat(entry.ts) < since:
                break
            if counts(entry.action):
                n += 1
        return n

    def after_block(self, decision: Decision, turn: TurnContext) -> str | None:
        """Called by the gateway after it recorded a blocked decision. Returns what the owner is told
        when this block tripped the breaker, else None."""
        if (not self.enabled or decision.outcome is not Outcome.BLOCK
                or decision.action.kind.value not in COUNTED_KINDS or self.kill_switch.is_on()):
            return None
        n = self.recent_blocks()
        if n < self.blocks:
            return None
        minutes = int(self.window.total_seconds() // 60)
        reason = (f"Safety breaker: {n} blocked attempts to move money or change settings in {minutes} minutes "
                  f"(the last one: {decision.action.summary}).")
        result = self.kill_switch.activate(reason=reason, actor="safety_breaker", channel=turn.channel,
                                           request_id=turn.request_id)
        told = (f"The safety breaker turned STOP on after {n} blocked attempts in {minutes} minutes. Every payment "
                "and outgoing message is frozen until you check what happened and resume with your PIN.")
        self.recorder.alert(request_id=turn.request_id, channel=turn.channel,
                            summary=f"Safety breaker: STOP turned on after {n} blocked attempts",
                            reason=f"{told} {result.message}", decision_id=decision.decision_id)
        return told
