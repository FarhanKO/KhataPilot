"""Money in from a customer who owes baki: the chat shows two buttons, and the owner's tap answers here.

The SMS was held unbooked when it was forwarded (record_sms asked instead of guessing). Answering books
exactly that SMS, re-read by the strict parser, and records the choice in the audit log against the
decision that asked. Only the owner reaches these endpoints, never the model.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.ids import new_id
from app.core.provenance import Sender
from app.guard.gateway import ToolRefusal
from app.guard.service import Guard
from app.guard.tools import answer_sms_question
from app.interface.deps import get_container, require_owner
from app.modules.chat.service import ChatService
from app.modules.ledger.schemas import SmsQuestionView

router = APIRouter(prefix="/sms-questions", tags=["sms-questions"])


class AnswerRequest(BaseModel):
    purpose: Literal["baki", "other"]


class AnswerResult(BaseModel):
    question: SmsQuestionView
    message: str
    data: dict


@router.get("", response_model=list[SmsQuestionView])
def questions(container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> list[SmsQuestionView]:
    with container.db.session() as session:
        return Guard(session, container.guard_deps).ledger.sms_questions(container.settings.shop_id)


@router.post("/{question_id}/answer", response_model=AnswerResult)
def answer(question_id: str, body: AnswerRequest, container=Depends(get_container),
           _owner: Sender = Depends(require_owner)) -> AnswerResult:
    request_id = new_id("req")
    with container.db.session() as session:
        guard = Guard(session, container.guard_deps)
        try:
            view, data, message = answer_sms_question(guard, question_id, body.purpose, request_id=request_id)
        except ToolRefusal as refusal:
            raise HTTPException(409, str(refusal)) from None
        if row := guard.inbox.by_question(question_id):  # an SMS from the owner's phone: now booked
            guard.inbox.settle(row, "booked", message, resolved=True)
        if view.conversation_id and view.conversation_id != "phone-sms":  # the model's history matches the choice
            what = "the SMS question" if view.source == "sms" else f"the question about money from {view.customer_name}"
            text = f"Update from the app: you answered {what}. {message}"
            ChatService(session, container.clock, container.settings.shop_id).append(
                view.conversation_id, "assistant", text, display=text, sender="app", request_id=request_id)
        return AnswerResult(question=view, message=message, data=data)
