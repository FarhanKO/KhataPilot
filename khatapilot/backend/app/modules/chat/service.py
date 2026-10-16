"""Public API of the chat module. History is append-only: the model sees earlier turns exactly as
they were first sent, which keeps prompt caching and Claude's thinking-block checks happy."""

from __future__ import annotations

from datetime import datetime

from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.modules.chat.models import ChatMessage, ChatTurnCards

TITLE_LENGTH = 80


class ChatMessageView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    seq: int
    conversation_id: str
    role: str
    content: str
    display: str
    sender: str
    request_id: str | None
    created_at: datetime


class ConversationSummary(BaseModel):
    id: str
    title: str  # the owner's first message
    started_at: datetime
    last_at: datetime
    messages: int


class ShownMessage(BaseModel):
    """One message as the chat showed it, with its cards."""

    role: str
    text: str
    sender: str
    request_id: str | None
    at: datetime
    forwarded: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []


class ChatService:
    def __init__(self, session: Session, clock: Clock, shop_id: str) -> None:
        self.session = session
        self.clock = clock
        self.shop_id = shop_id

    def append(self, conversation_id: str, role: str, content: str, *, display: str, sender: str,
               request_id: str | None = None) -> ChatMessageView:
        row = ChatMessage(shop_id=self.shop_id, conversation_id=conversation_id, role=role, content=content,
                          display=display, sender=sender, request_id=request_id, created_at=self.clock.now())
        self.session.add(row)
        self.session.flush()
        return ChatMessageView.model_validate(row)

    def history(self, conversation_id: str, limit: int = 40) -> list[ChatMessageView]:
        rows = self.session.scalars(
            select(ChatMessage)
            .where(ChatMessage.shop_id == self.shop_id, ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.seq.desc())
            .limit(limit)
        ).all()
        return [ChatMessageView.model_validate(r) for r in reversed(rows)]

    # ---- earlier chats, for the owner's "Past chats" list

    def save_cards(self, conversation_id: str, request_id: str, *, forwarded: list[dict[str, Any]],
                   outcomes: list[dict[str, Any]]) -> None:
        if forwarded or outcomes:
            self.session.merge(ChatTurnCards(request_id=request_id, shop_id=self.shop_id,
                                             conversation_id=conversation_id, forwarded=forwarded,
                                             outcomes=outcomes))

    def _owner_conversations(self):
        """Conversations the owner started in the app; messages from other people live elsewhere."""
        return (ChatMessage.shop_id == self.shop_id, ChatMessage.sender == "owner")

    def conversations(self, limit: int = 100) -> list[ConversationSummary]:
        rows = self.session.execute(
            select(ChatMessage.conversation_id, func.min(ChatMessage.created_at), func.max(ChatMessage.created_at),
                   func.count())
            .where(ChatMessage.shop_id == self.shop_id,
                   ChatMessage.conversation_id.in_(select(ChatMessage.conversation_id)
                                                   .where(*self._owner_conversations())))
            .group_by(ChatMessage.conversation_id)
            .order_by(func.max(ChatMessage.seq).desc())
            .limit(limit)
        ).all()
        firsts = dict(self.session.execute(
            select(ChatMessage.conversation_id, ChatMessage.display)
            .where(*self._owner_conversations(), ChatMessage.seq.in_(
                select(func.min(ChatMessage.seq)).where(*self._owner_conversations())
                .group_by(ChatMessage.conversation_id)))
        ).all())
        return [ConversationSummary(id=cid, title=_title(firsts.get(cid, "")), started_at=started, last_at=last,
                                    messages=count)
                for cid, started, last, count in rows]

    def shown(self, conversation_id: str) -> list[ShownMessage]:
        rows = self.session.scalars(
            select(ChatMessage).where(ChatMessage.shop_id == self.shop_id,
                                      ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.seq)
        ).all()
        cards = {c.request_id: c for c in self.session.scalars(
            select(ChatTurnCards).where(ChatTurnCards.shop_id == self.shop_id,
                                        ChatTurnCards.conversation_id == conversation_id))}
        shown = []
        for row in rows:
            card = cards.get(row.request_id or "")
            shown.append(ShownMessage(
                role=row.role, text=row.display, sender=row.sender, request_id=row.request_id, at=row.created_at,
                forwarded=card.forwarded if card and row.role == "user" else [],
                outcomes=card.outcomes if card and row.role == "assistant" else []))
        return shown

    def delete(self, conversation_id: str) -> int:
        """Forget an owner's conversation. The audit log keeps every decision regardless."""
        owned = self.session.scalar(select(func.count()).where(*self._owner_conversations(),
                                                               ChatMessage.conversation_id == conversation_id))
        if not owned:
            return 0
        self.session.execute(delete(ChatTurnCards).where(ChatTurnCards.shop_id == self.shop_id,
                                                         ChatTurnCards.conversation_id == conversation_id))
        result = self.session.execute(delete(ChatMessage).where(ChatMessage.shop_id == self.shop_id,
                                                                ChatMessage.conversation_id == conversation_id))
        return result.rowcount or 0


def _title(display: str) -> str:
    text = " ".join(display.split())
    return text if len(text) <= TITLE_LENGTH else text[: TITLE_LENGTH - 1].rstrip() + "…"
