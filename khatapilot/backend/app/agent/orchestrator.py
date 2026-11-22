"""The agent loop. Bounded, and every tool call goes through the guard's gateway.

    quarantine untrusted blocks -> render the turn -> ask the model -> submit each proposed tool call
    to the guard -> hand the results back as data -> ... until the model answers (or the step limit)
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.agent.llm.mock_provider import HELP_REPLY
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
from app.agent.routing import Route, choose
from app.core.clock import local_date
from app.core.provenance import Provenance, SenderKind, TurnContext
from app.guard.api import ToolCall, ToolOutcome, ToolPort

REFUSAL_REPLY = ("I can't help with that one, so I didn't do anything. If it's about the shop's money or "
                 "books, try asking in a different way.")
OFFLINE_REPLY = ("The AI model is offline right now, so I can't get your data for that request. Nothing was "
                 "changed. Simple requests still work offline, like \"today's sales\", \"who owes me?\", \"will I run "
                 "short of cash?\" or \"sold 3 bags of rice ৳1,500 cash\". Try again in a little while for the rest.")
CAPPED_REPLY = ("This month's AI budget is used up, so I can only use the offline rules, and they didn't understand "
                "that. Nothing was changed. Simple requests still work, like \"today's sales\" or \"who owes me?\".")

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
    # Who planned the turn: "model" (the LLM), "rules" (the offline planner, chosen up front: no API cost),
    # "offline" (the LLM could not be reached, so the offline planner answered instead) or "capped" (this
    # month's AI budget is used up, so the offline planner answers until the month ends).
    route: str = "model"
    route_reason: str = ""


def outcome_content(outcome: ToolOutcome) -> str:
    """What the model gets back: data only, never anything it could mistake for authority."""
    return json.dumps({
        "status": outcome.status, "tier": outcome.tier, "message": outcome.message, "data": outcome.data,
        "pending_id": outcome.pending_id, "requires_pin": outcome.requires_pin,
    }, ensure_ascii=False, default=str)


class Orchestrator:
    """`rules` is the offline planner. When given, `rules_first` lets it take the plain turns
    (app/agent/routing.py) and `offline_fallback` lets it answer when `provider` cannot be reached."""

    def __init__(self, provider: LLMProvider, *, max_steps: int = 6, rules: LLMProvider | None = None,
                 rules_first: bool = False, offline_fallback: bool = False) -> None:
        self.provider = provider
        self.max_steps = max_steps
        self.rules = rules
        self.rules_first = rules_first and rules is not None
        self.offline_fallback = offline_fallback and rules is not None
        # turns per route since start, for /llm/usage
        self.routes = {"model": 0, "rules": 0, "offline": 0, "capped": 0}

    def handle(self, turn: TurnContext, *, history: list[HistoryMessage], port: ToolPort, shop_name: str,
               on_event: EventSink | None = None, capped: str | None = None) -> AgentReply:
        """`capped`: why the model may not be used this turn (the month's budget is spent); the offline
        planner then answers the whole turn, including reading forwarded invoices."""
        reply = self._handle(turn, history=history, port=port, shop_name=shop_name,
                             emit=on_event or (lambda _event: None),
                             capped=capped if capped and self.rules is not None else None)
        self.routes[reply.route] += 1
        return reply

    def _handle(self, turn: TurnContext, *, history: list[HistoryMessage], port: ToolPort, shop_name: str,
                emit: EventSink, capped: str | None) -> AgentReply:
        owner_turn = turn.sender.kind is SenderKind.OWNER
        untrusted = [(i, s) for i, s in enumerate(turn.segments, start=1) if s.provenance is Provenance.UNTRUSTED]
        reader = self.rules if capped else self.provider
        reports = [quarantine(block, segment, reader) for block, segment in untrusted]
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
        provider, route = self.provider, Route("model", "")
        if capped:
            provider, route = self.rules, Route("capped", capped)
        elif self.rules_first:
            route = choose(brief, history)
            if route.planner == "rules":
                provider = self.rules
        session = provider.session(system=SYSTEM_PROMPT, tools=tools, history=history)
        session.user(rendered, brief)

        reply = AgentReply(text="", rendered_user=rendered, quarantine=reports, model=provider.model,
                           route=route.planner, route_reason=route.reason)
        if self.rules_first or capped:
            emit({"type": "llm_route", "request_id": turn.request_id, "route": route.planner, "reason": route.reason})
        step = 0
        while step < self.max_steps:
            step += 1
            reply.steps = step
            try:
                result = session.step()
            except LLMError as error:
                # Fall back only before anything ran: a fresh planner would redo what the model already did.
                if self.offline_fallback and error.transient and reply.route == "model" and not reply.outcomes:
                    reply.route, reply.route_reason = "offline", str(error)
                    emit({"type": "llm_route", "request_id": turn.request_id, "route": "offline",
                          "reason": str(error)})
                    session = self.rules.session(system=SYSTEM_PROMPT, tools=tools, history=history)
                    session.user(rendered, brief)
                    step = 0
                    continue
                # A network or service hiccup gets a calm reply; the technical reason goes to the trace.
                # A setup mistake (a bad key or model name) is said as it is: only the owner can fix it.
                calm = error.transient and not reply.outcomes  # after a tool ran, say exactly what stopped
                reply.text = OFFLINE_REPLY if calm else f"{error} Nothing was changed."
                if calm:
                    reply.route, reply.route_reason = "offline", str(error)
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
                if reply.route in ("offline", "capped") and not reply.outcomes and reply.text == HELP_REPLY:
                    # the offline rules stood in and did not understand: say so plainly, not a generic help text
                    reply.text = OFFLINE_REPLY if reply.route == "offline" else CAPPED_REPLY
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
