from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_conversation", "conversation_id", "seq"),)

    seq: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    shop_id: Mapped[str] = mapped_column(String(64))
    conversation_id: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(12))  # user | assistant
    content: Mapped[str] = mapped_column(Text)  # exactly what the model saw or said (keeps the prefix stable)
    display: Mapped[str] = mapped_column(Text)  # what the chat UI shows
    sender: Mapped[str] = mapped_column(String(64))  # owner | external:<masked> | assistant
    request_id: Mapped[str | None] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime]


class ChatTurnCards(Base):
    """What the chat showed around a turn besides the words: the blocks the owner forwarded and the
    result cards of each action. Only for showing an earlier chat again; the model never reads it.
    A table of its own, so an existing database gains it without a migration."""

    __tablename__ = "chat_turn_cards"

    request_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    shop_id: Mapped[str] = mapped_column(String(64))
    conversation_id: Mapped[str] = mapped_column(String(64), index=True)
    forwarded: Mapped[list] = mapped_column(JSON, default=list)
    outcomes: Mapped[list] = mapped_column(JSON, default=list)
