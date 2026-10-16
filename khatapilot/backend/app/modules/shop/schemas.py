from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.core.masking import mask_phone, normalize_msisdn


class ShopProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    owner_name: str
    shop_type: str
    area: str
    district: str
    division: str
    urbanicity: str
    owner_phone: str
    bkash_account: str
    nagad_account: str
    bank_account: str
    data_since: date


class PartyView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: Literal["supplier", "customer"]
    name: str
    number: str | None
    method: str | None
    category: str | None
    terms_days: int | None

    @property
    def number_masked(self) -> str | None:
        if self.number is None:
            return None
        return mask_phone(self.number) if normalize_msisdn(self.number) else f"****{self.number[-4:]}"


class NewParty(BaseModel):
    kind: Literal["supplier", "customer"]
    name: str
    number: str | None = None
    method: str | None = None
    category: str | None = None
    terms_days: int | None = None
    notes: str | None = None
