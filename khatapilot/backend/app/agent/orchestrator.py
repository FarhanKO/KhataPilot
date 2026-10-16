"""The agent loop. Bounded, and every tool call goes through the guard's gateway.

    quarantine untrusted blocks -> render the turn -> ask the model -> submit each proposed tool call
    to the guard -> hand the results back as data -> ... until the model answers (or the step limit)
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.agent.llm.schema import clean_schema
from app.agent.llm.types import (
    HistoryMessage,
    LLMError,
    LLMProvider,
    LLMTool,
    SegmentBrief,
    ToolResult,
    TurnBrief,
)
from app.agent.prompts import SYSTEM_PROMPT, render_turn
from app.agent.quarantine import QuarantineReport, quarantine
from app.core.clock import local_date
from app.core.provenance import Provenance, SenderKind, TurnContext
from app.guard.api import ToolCall, ToolOutcome, ToolPort

REFUSAL_REPLY = ("I can't help with that one, so I didn't do anything. If it's about the shop's money or "
                 "books, try asking in a different way.")

EventSink = Callable[[dict[str, Any]], None]


@dataclass
class AgentReply:
    text: str
    outcomes: list[ToolOutcome] = field(default_factory=list)
    rendered_user: str = ""
    quarantine: list[QuarantineReport] = field(default_factory=list)
    steps: int = 0
    model: str = ""
    stopped_early: bool = False


def outcome_content(outcome: ToolOutcome) -> str:
    """What the model gets back: data only, never anything it could mistake for authority."""
    return json.dumps({
        "status": outcome.status, "tier": outcome.tier, "message": outcome.message, "data": outcome.data,
        "pending_id": outcome.pending_id, "requires_pin": outcome.requires_pin,
    }, ensure_ascii=False, default=str)


class Orchestrator:
    def __init__(self, provider: LLMProvider, *, max_steps: int = 6) -> None:
        self.provider = provider
        self.max_steps = max_steps

    def handle(self, turn: TurnContext, *, history: list[HistoryMessage], port: ToolPort, shop_name: str,
               on_event: EventSink | None = None) -> AgentReply:
        emit = on_event or (lambda _event: None)
        owner_turn = turn.sender.kind is SenderKind.OWNER
        untrusted = [(i, s) for i, s in enumerate(turn.segments, start=1) if s.provenance is Provenance.UNTRUSTED]
        reports = [quarantine(block, segment, self.provider) for block, segment in untrusted]
        for report in reports:
            emit({"type": "quarantine", "request_id": turn.request_id, "block": report.block,
                  "kind": report.kind, "summary": report.describe(), "suspected": report.injection.suspected})

        rendered = render_turn(
            now=turn.received_at, shop_name=shop_name, sender_kind="owner" if owner_turn else "external",
            sender_display=turn.sender.display, channel=turn.channel, owner_text=turn.owner_text,
            untrusted=[(block, s.source, s.text) for block, s in untrusted],
            reports={r.block: r.describe() for r in reports},
        )
        by_block = {r.block: r for r in reports}
        brief = TurnBrief(
            sender="owner" if owner_turn else "external",
            sender_display=turn.sender.display,
            owner_text=turn.owner_text,
            segments=tuple(
                SegmentBrief(index=i, source=s.source, text=s.text,
                             kind=by_block[i].kind if i in by_block else "owner_text",
                             parsed=by_block[i].parsed if i in by_block else None,
                             suspected_injection=by_block[i].injection.suspected if i in by_block else False)
                for i, s in enumerate(turn.segments, start=1)
            ),
            today=local_date(turn.received_at),
            now=turn.received_at,
        )
        tools = [LLMTool(d.name, d.description, clean_schema(d.parameters)) for d in port.descriptors()]
        session = self.provider.session(system=SYSTEM_PROMPT, tools=tools, history=history)
        session.user(rendered, brief)

        reply = AgentReply(text="", rendered_user=rendered, quarantine=reports, model=self.provider.model)
        for step in range(1, self.max_steps + 1):
            reply.steps = step
            try:
                result = session.step()
            except LLMError as error:
                reply.text = f"{error} Nothing was changed."
                emit({"type": "llm_error", "request_id": turn.request_id, "message": str(error)})
                return reply
            reply.model = result.model
            emit({"type": "llm_step", "request_id": turn.request_id, "step": step, "model": result.model,
                  "stop_reason": result.stop_reason, "tool_calls": [c.name for c in result.tool_calls],
                  "usage": result.usage})
            if result.stop_reason == "refusal":
                reply.text = REFUSAL_REPLY
                return reply
            if not result.tool_calls:
                reply.text = result.text or "Done."
                return reply
            results = []
            for proposed in result.tool_calls:
                outcome = port.submit(ToolCall(name=proposed.name, arguments=proposed.arguments,
                                               call_id=proposed.id), turn)
                reply.outcomes.append(outcome)
                emit({"type": "tool", "request_id": turn.request_id, "tool": proposed.name, "status": outcome.status,
                      "tier": outcome.tier, "decision_id": outcome.decision_id})
                results.append(ToolResult(call_id=proposed.id, content=outcome_content(outcome),
                                          is_error=outcome.status == "error", outcome=outcome))
            session.tool_results(results)
        reply.stopped_early = True
        done = "; ".join(f"{o.tool}: {o.status}" for o in reply.outcomes) or "nothing"
        reply.text = f"I stopped after {self.max_steps} steps to stay safe. What happened: {done}."
        return reply
