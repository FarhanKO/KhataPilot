"""The owner's chat, invoice photos, and the simulated inbound channel for messages from other people.

Before any model is involved, this layer:
- labels every piece of text (owner's own words vs. forwarded or pasted content);
- handles "stop" itself, deterministically, so the kill switch never depends on the model;
- refuses to store or forward a bare 4-6 digit number (it looks like a PIN), or a message that names
  a PIN, OTP or password next to a 4-6 digit number ("my pin is 4821"); the number is removed and the
  model never sees the message.
"""

from __future__ import annotations

import base64
import binascii
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from app.agent.llm.types import HistoryMessage, LLMError
from app.agent.photo import read_invoice_photo
from app.core.ids import new_id
from app.core.masking import mask_phone, mask_text, normalize_msisdn
from app.core.money import normalize_digits
from app.core.provenance import Provenance, Segment, Sender, SenderKind, TurnContext
from app.core.sms import split_pasted_sms
from app.guard.approvals import PendingView
from app.guard.gateway import ToolOutcome
from app.guard.owner_controls import OwnerControls
from app.guard.service import Guard
from app.interface.deps import get_container, require_owner
from app.modules.chat.service import ChatService, ConversationSummary, ShownMessage

router = APIRouter(tags=["chat"])

STOP_WORDS = {"stop", "stop everything", "freeze", "kill switch", "thamo", "thamao", "bondho", "bondho koro",
              "sob bondho", "থামো", "বন্ধ", "বন্ধ করো", "সব বন্ধ"}
PIN_LIKE = re.compile(r"^\s*\d{4,6}\s*$")
SECRET_WORD = r"(?:pin|p\.i\.n\.?|otp|password|passcode|pass\s*code|পিন|পাসওয়ার্ড|ওটিপি)"
# "pin is 4821", "PIN: 4821", "amar pin holo 4821", "4821 is my pin", "4821 amar pin"
PIN_IN_WORDS = re.compile(
    rf"(?<![\w]){SECRET_WORD}(?![\w])\W{{0,3}}(?:[^\W\d]+\W+){{0,3}}?(?P<a>\d{{4,6}})(?!\d)"
    rf"|(?<!\d)(?P<b>\d{{4,6}})\s+(?:(?:is|holo|hocche)\s+(?:my|amar)|amar|আমার)\s+{SECRET_WORD}(?![\w])",
    re.IGNORECASE,
)
PIN_REPLY = ("Please never type your PIN in chat - I removed that message and did not keep it. The PIN only goes "
             "into the PIN box when you approve something.")


def redact_pin_mentions(text: str) -> tuple[str, bool]:
    """Replace a number the owner calls their PIN, OTP or password with ••••. Returns (text, found)."""
    found = False

    def hide(match: re.Match[str]) -> str:
        nonlocal found
        found = True
        digits = match.group("a") or match.group("b")
        return match.group(0).replace(digits, "••••")

    return PIN_IN_WORDS.sub(hide, text), found
CONVERSATION_ID = r"^[A-Za-z0-9_\-:]{1,64}$"


class ForwardedBlock(BaseModel):
    source: Literal["forwarded_sms", "invoice_text"] = "forwarded_sms"
    text: str = Field(min_length=1, max_length=2000)


class ChatRequest(BaseModel):
    conversation_id: str | None = Field(default=None, pattern=CONVERSATION_ID)
    message: str = Field(default="", max_length=2000)
    # A day's invoice photos, read at closing time, go in one message.
    forwarded: list[ForwardedBlock] = Field(default_factory=list, max_length=20)


class InboundRequest(BaseModel):
    from_number: str = Field(max_length=20)
    text: str = Field(min_length=1, max_length=2000)
    channel: Literal["sms_gateway", "messenger"] = "sms_gateway"


class PhotoRequest(BaseModel):
    image_base64: str = Field(min_length=1, max_length=7_000_000)  # 5 MB once decoded


class PhotoResponse(BaseModel):
    readable: bool
    text: str
    truncated: bool
    message: str
    model: str


class ChatResponse(BaseModel):
    conversation_id: str
    request_id: str
    reply: str
    outcomes: list[ToolOutcome] = []
    pending: list[PendingView] = []
    quarantine: list[dict] = []
    kill_switch: bool = False
    model: str = ""


def _normal(text: str) -> str:
    return re.sub(r"[^\w\sঀ-৿]", "", text.lower()).strip()


def _remember(container, conversation_id: str, request_id: str, user_content: str, user_display: str,
              sender: str, reply: str, *, forwarded: list[dict] | None = None,
              outcomes: list[ToolOutcome] | None = None) -> None:
    with container.db.session() as session:
        chat = ChatService(session, container.clock, container.settings.shop_id)
        chat.append(conversation_id, "user", user_content, display=user_display, sender=sender, request_id=request_id)
        chat.append(conversation_id, "assistant", reply, display=reply, sender="assistant", request_id=request_id)
        chat.save_cards(conversation_id, request_id, forwarded=forwarded or [],
                        outcomes=[o.model_dump(mode="json") for o in outcomes or []])


def _trace_turn(container, turn: TurnContext) -> None:
    """What was asked and by whom, for the decision-trace panel (numbers masked, long text cut)."""
    container.trace.publish({
        "type": "turn", "request_id": turn.request_id, "channel": turn.channel, "ts": turn.received_at.isoformat(),
        "sender": "owner" if turn.is_owner_turn else "external", "sender_display": turn.sender.display,
        "segments": [{"source": s.source, "provenance": s.provenance.value, "text": mask_text(s.text)[:600]}
                     for s in turn.segments],
    })


def _trace_reply(container, request_id: str, text: str, *, kill_switch: bool = False) -> None:
    container.trace.publish({"type": "reply", "request_id": request_id, "text": text, "kill_switch": kill_switch})


def _owner_turn(container, request_id: str, owner: Sender, text: str) -> TurnContext:
    return TurnContext(request_id=request_id, conversation_id="-", shop_id=container.settings.shop_id, channel="web",
                       sender=owner, segments=(Segment(text=text, provenance=Provenance.OWNER, source="chat"),),
                       received_at=container.clock.now())


def _run_agent(container, turn: TurnContext, *, conversation_id: str, user_display: str, sender: str,
               forwarded: list[dict] | None = None) -> ChatResponse:
    _trace_turn(container, turn)
    with container.db.session() as session:
        history = [HistoryMessage(role=m.role, content=m.content)
                   for m in ChatService(session, container.clock, container.settings.shop_id).history(conversation_id)]
        shop = Guard(session, container.guard_deps).shops.get(container.settings.shop_id)
    reply = container.agent.handle(turn, history=history, port=container.tool_port(),
                                   shop_name=shop.name if shop else "the shop", on_event=container.trace.publish)
    _remember(container, conversation_id, turn.request_id, reply.rendered_user, user_display, sender, reply.text,
              forwarded=forwarded, outcomes=reply.outcomes)
    decisions = {o.decision_id for o in reply.outcomes}
    with container.db.session() as session:
        guard = Guard(session, container.guard_deps)
        pending = [p for p in guard.approvals.pending() if p.decision_id in decisions]
        frozen = guard.store.kill_switch().frozen
    _trace_reply(container, turn.request_id, reply.text, kill_switch=frozen)
    return ChatResponse(
        conversation_id=conversation_id, request_id=turn.request_id, reply=reply.text, outcomes=reply.outcomes,
        pending=pending, kill_switch=frozen, model=reply.model,
        quarantine=[{"block": q.block, "kind": q.kind, "summary": q.describe(), "suspected": q.injection.suspected}
                    for q in reply.quarantine],
    )


@router.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest, container=Depends(get_container), owner: Sender = Depends(require_owner)) -> ChatResponse:
    request_id = new_id("req")
    conversation_id = body.conversation_id or new_id("conv")
    text = normalize_digits(body.message).strip()

    if PIN_LIKE.match(text) and not body.forwarded:
        _remember(container, conversation_id, request_id, "[removed: the owner typed a number that looked like a PIN]",
                  "••••", "owner", PIN_REPLY)
        _trace_turn(container, _owner_turn(container, request_id, owner, "•••• (looked like a PIN; not kept)"))
        _trace_reply(container, request_id, PIN_REPLY)
        return ChatResponse(conversation_id=conversation_id, request_id=request_id, reply=PIN_REPLY)

    redacted, named_pin = redact_pin_mentions(text)
    if named_pin:  # the model never sees this message; only the redacted text is kept
        _remember(container, conversation_id, request_id, f"[not sent to the model: it named a PIN] {redacted}",
                  redacted, "owner", PIN_REPLY)
        _trace_turn(container, _owner_turn(container, request_id, owner, f"{redacted} (named a PIN; number removed)"))
        _trace_reply(container, request_id, PIN_REPLY)
        return ChatResponse(conversation_id=conversation_id, request_id=request_id, reply=PIN_REPLY)

    if _normal(text) in STOP_WORDS and not body.forwarded:
        _trace_turn(container, _owner_turn(container, request_id, owner, body.message))
        with container.db.session() as session:
            guard = Guard(session, container.guard_deps)
            controls = OwnerControls(clock=container.clock, store=guard.store, pins=guard.pins,
                                     kill_switch=guard.kill_switch, recorder=guard.recorder)
            result = controls.stop(reason="The owner typed STOP in chat", request_id=request_id, channel="web")
        _remember(container, conversation_id, request_id, body.message, body.message, "owner", result.message)
        _trace_reply(container, request_id, result.message, kill_switch=True)
        return ChatResponse(conversation_id=conversation_id, request_id=request_id, reply=result.message,
                            kill_switch=True)

    owner_words, pasted = split_pasted_sms(text)
    segments = []
    if owner_words:
        segments.append(Segment(text=owner_words, provenance=Provenance.OWNER, source="chat"))
    if pasted:  # an SMS pasted into the owner's own message is still untrusted content
        segments.append(Segment(text=pasted, provenance=Provenance.UNTRUSTED, source="forwarded_sms"))
    segments += [Segment(text=f.text, provenance=Provenance.UNTRUSTED, source=f.source) for f in body.forwarded]
    if not segments:
        raise HTTPException(422, "Send a message or forward something.")
    turn = TurnContext(request_id=request_id, conversation_id=conversation_id, shop_id=container.settings.shop_id,
                       channel="web", sender=owner, segments=tuple(segments), received_at=container.clock.now())
    display = body.message or f"(forwarded {len(body.forwarded)} item{'s' if len(body.forwarded) != 1 else ''})"
    return _run_agent(container, turn, conversation_id=conversation_id, user_display=display, sender="owner",
                      forwarded=[f.model_dump() for f in body.forwarded])


@router.get("/chat/conversations", response_model=list[ConversationSummary])
def conversations(container=Depends(get_container), _owner: Sender = Depends(require_owner)):
    """The owner's earlier chats, latest first."""
    with container.db.session() as session:
        return ChatService(session, container.clock, container.settings.shop_id).conversations()


@router.get("/chat/conversations/{conversation_id}", response_model=list[ShownMessage])
def conversation(conversation_id: str = Path(pattern=CONVERSATION_ID), container=Depends(get_container),
                 _owner: Sender = Depends(require_owner)):
    """One earlier chat as it was shown, to open it again and carry on."""
    with container.db.session() as session:
        return ChatService(session, container.clock, container.settings.shop_id).shown(conversation_id)


@router.delete("/chat/conversations/{conversation_id}")
def delete_conversation(conversation_id: str = Path(pattern=CONVERSATION_ID), container=Depends(get_container),
                        _owner: Sender = Depends(require_owner)) -> dict:
    """Forget one of the owner's chats. Payments, approvals and every guard decision stay in the audit log."""
    with container.db.session() as session:
        deleted = ChatService(session, container.clock, container.settings.shop_id).delete(conversation_id)
    if not deleted:
        raise HTTPException(404, "No chat of yours with that id.")
    return {"deleted": deleted}


@router.post("/chat/photo", response_model=PhotoResponse)
def read_photo(body: PhotoRequest, container=Depends(get_container),
               _owner: Sender = Depends(require_owner)) -> PhotoResponse:
    """Copy the text from an invoice photo and hand it back to the owner to check. Nothing is
    recorded, paid or sent to the agent here: the owner forwards the checked text as invoice text."""
    try:
        image = base64.b64decode(body.image_base64, validate=True)
    except binascii.Error as exc:
        raise HTTPException(422, "The photo did not arrive intact. Please try again.") from exc
    try:
        reading = read_invoice_photo(container.provider, image)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(503, str(exc)) from exc
    return PhotoResponse(readable=reading.readable, text=reading.text, truncated=reading.truncated,
                         message=reading.message, model=container.provider.model)


@router.post("/sim/inbound", response_model=ChatResponse)
def inbound(body: InboundRequest, container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> ChatResponse:
    """Demo stand-in for the messaging gateway's webhook: a message from someone who is NOT the owner
    (a supplier, a customer, a stranger). The attack simulator in the UI uses it."""
    number = normalize_msisdn(body.from_number)
    if number is None:
        raise HTTPException(422, "from_number must be a Bangladeshi mobile number.")
    sender = Sender(kind=SenderKind.EXTERNAL, display=mask_phone(number))
    request_id = new_id("req")
    turn = TurnContext(request_id=request_id, conversation_id=f"inbound-{number}", shop_id=container.settings.shop_id,
                       channel=body.channel, sender=sender,
                       segments=(Segment(text=body.text, provenance=Provenance.UNTRUSTED, source="inbound_message"),),
                       received_at=container.clock.now())
    return _run_agent(container, turn, conversation_id=f"inbound-{number}", user_display=body.text,
                      sender=f"external:{sender.display}")
