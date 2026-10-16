"""Public API of the shop module."""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.ids import new_id
from app.modules.shop.models import Party, Shop
from app.modules.shop.schemas import NewParty, PartyView, ShopProfile


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


class ShopService:
    def __init__(self, session: Session, clock: Clock) -> None:
        self.session = session
        self.clock = clock

    def create_shop(self, profile: ShopProfile) -> ShopProfile:
        self.session.add(Shop(created_at=self.clock.now(), **profile.model_dump()))
        return profile

    def get(self, shop_id: str) -> ShopProfile | None:
        shop = self.session.get(Shop, shop_id)
        return ShopProfile.model_validate(shop) if shop else None

    def add_party(self, shop_id: str, party: NewParty) -> PartyView:
        row = Party(id=new_id("party"), shop_id=shop_id, created_at=self.clock.now(), **party.model_dump())
        self.session.add(row)
        self.session.flush()
        return PartyView.model_validate(row)

    def parties(self, shop_id: str, kind: str | None = None) -> list[PartyView]:
        query = select(Party).where(Party.shop_id == shop_id).order_by(Party.name)
        if kind:
            query = query.where(Party.kind == kind)
        return [PartyView.model_validate(p) for p in self.session.scalars(query)]

    def find_party_by_number(self, shop_id: str, number: str | None, kind: str | None = None) -> PartyView | None:
        if not number:
            return None
        query = select(Party).where(Party.shop_id == shop_id, Party.number == number)
        if kind:
            query = query.where(Party.kind == kind)
        row = self.session.scalars(query.limit(1)).first()
        return PartyView.model_validate(row) if row else None

    def find_party(self, shop_id: str, name: str, kind: str | None = None) -> PartyView | None:
        """Case- and punctuation-insensitive name match; a unique prefix also matches."""
        wanted = _norm(name)
        candidates = self.parties(shop_id, kind)
        exact = [p for p in candidates if _norm(p.name) == wanted]
        if exact:
            return exact[0]
        prefix = [p for p in candidates if _norm(p.name).startswith(wanted)]
        return prefix[0] if len(prefix) == 1 else None
