"""What the AI model cost each shop each calendar month, kept in the shop database.

Every call to the model adds its tokens and its estimated cost here (the provider reports each call as
it returns, so extraction and photo reads count as well as chat steps), and every chat turn adds one to
the count of whoever planned it: the model, the offline rules (free), the offline fallback, or the
offline planner because the month's budget was used up. Counters are added with one UPDATE, so two
requests at once never lose a count.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import String, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.infra.db import Base

ROUTES = ("model", "rules", "offline", "capped")


class LlmUsageMonth(Base):
    __tablename__ = "llm_usage_months"

    shop_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    month: Mapped[str] = mapped_column(String(7), primary_key=True)  # "2026-11"
    calls: Mapped[int] = mapped_column(default=0)
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    cache_read_tokens: Mapped[int] = mapped_column(default=0)
    cache_write_tokens: Mapped[int] = mapped_column(default=0)
    cost_micro_usd: Mapped[int] = mapped_column(default=0)  # millionths of a US$, at list prices
    unpriced_calls: Mapped[int] = mapped_column(default=0)  # calls to a model with no known price
    turns_model: Mapped[int] = mapped_column(default=0)
    turns_rules: Mapped[int] = mapped_column(default=0)
    turns_offline: Mapped[int] = mapped_column(default=0)
    turns_capped: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime | None] = mapped_column(default=None)


@dataclass(frozen=True)
class MonthUsage:
    shop_id: str
    month: str
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    unpriced_calls: int = 0
    turns: dict[str, int] | None = None

    @property
    def total_turns(self) -> int:
        return sum((self.turns or {}).values())


def _add(session: Session, shop_id: str, month: str, values: dict[str, int]) -> None:
    columns = {name: getattr(LlmUsageMonth, name) + amount for name, amount in values.items()}
    now = datetime.now(UTC)
    where = (LlmUsageMonth.shop_id == shop_id, LlmUsageMonth.month == month)
    if session.execute(update(LlmUsageMonth).where(*where).values(**columns, updated_at=now)).rowcount:
        return
    try:
        with session.begin_nested():
            session.add(LlmUsageMonth(shop_id=shop_id, month=month, updated_at=now, **values))
    except IntegrityError:  # another request made the row first: add to it
        session.execute(update(LlmUsageMonth).where(*where).values(**columns, updated_at=now))


def add_call(session: Session, shop_id: str, month: str, usage: dict[str, int], cost_usd: float | None) -> None:
    _add(session, shop_id, month, {
        "calls": 1,
        "input_tokens": int(usage.get("input_tokens", 0) or 0),
        "output_tokens": int(usage.get("output_tokens", 0) or 0),
        "cache_read_tokens": int(usage.get("cache_read_input_tokens", 0) or 0),
        "cache_write_tokens": int(usage.get("cache_creation_input_tokens", 0) or 0),
        "cost_micro_usd": round(cost_usd * 1_000_000) if cost_usd is not None else 0,
        "unpriced_calls": 0 if cost_usd is not None else 1,
    })


def add_turn(session: Session, shop_id: str, month: str, route: str) -> None:
    if route not in ROUTES:
        return  # "guard": answered before any planner (STOP, a PIN in chat)
    _add(session, shop_id, month, {f"turns_{route}": 1})


def month_usage(session: Session, shop_id: str, month: str) -> MonthUsage:
    row = session.get(LlmUsageMonth, (shop_id, month))
    if row is None:
        return MonthUsage(shop_id=shop_id, month=month, turns=dict.fromkeys(ROUTES, 0))
    return MonthUsage(
        shop_id=shop_id, month=month, calls=row.calls, input_tokens=row.input_tokens, output_tokens=row.output_tokens,
        cache_read_tokens=row.cache_read_tokens, cache_write_tokens=row.cache_write_tokens,
        cost_usd=row.cost_micro_usd / 1_000_000, unpriced_calls=row.unpriced_calls,
        turns={route: getattr(row, f"turns_{route}") for route in ROUTES})
