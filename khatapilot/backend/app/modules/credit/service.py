"""Public API of the credit module: consent, and an explained credit profile built from the books.

Nothing is built without the owner's consent, which only the owner can give through the app (no
agent tool grants it). Withdrawing consent deletes every stored profile.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.clock import Clock, local_date
from app.core.ids import new_id
from app.modules.credit import explain
from app.modules.credit.features import BY_NAME, NAMES, from_books, vector
from app.modules.credit.model import CreditModel, band_for  # CreditModel: public (the composition root loads it)
from app.modules.credit.models import CreditConsent, CreditProfileRecord
from app.modules.credit.schemas import (
    CONSENT_TEXT,
    CONSENT_VERSION,
    ConsentRequired,
    ConsentView,
    CreditProfile,
    FeatureView,
    NotEnoughHistory,
)
from app.modules.ledger.service import LedgerService
from app.modules.shop.service import ShopService


class CreditModelMissing(Exception):
    """No trained credit model is loaded (run scripts/train_credit.py)."""


def summary_text(probability: float, band: str, words: str) -> str:
    share = "fewer than 1" if probability < 0.01 else f"about {probability * 100:.0f}"
    return (f"Band {band} ({words}): {share} in 100 shops with books like yours would fall behind on a "
            "12-month working-capital loan.")


class CreditService:
    def __init__(self, session: Session, clock: Clock, shop_id: str, model: CreditModel | None) -> None:
        self.session = session
        self.clock = clock
        self.shop_id = shop_id
        self.model = model

    # ---- consent

    def _active_consent(self) -> CreditConsent | None:
        return self.session.scalars(
            select(CreditConsent).where(CreditConsent.shop_id == self.shop_id, CreditConsent.revoked_at.is_(None))
            .order_by(CreditConsent.granted_at.desc())
        ).first()

    def consent(self) -> ConsentView:
        row = self._active_consent()
        return ConsentView(granted=row is not None, version=CONSENT_VERSION, text=CONSENT_TEXT,
                           granted_at=row.granted_at if row else None)

    def grant_consent(self) -> ConsentView:
        if self._active_consent() is None:
            self.session.add(CreditConsent(id=new_id("cons"), shop_id=self.shop_id, version=CONSENT_VERSION,
                                           granted_at=self.clock.now()))
            self.session.flush()
        return self.consent()

    def withdraw_consent(self) -> int:
        """Withdraw consent and delete every stored profile. Returns how many profiles were deleted."""
        now = self.clock.now()
        for row in self.session.scalars(select(CreditConsent).where(
                CreditConsent.shop_id == self.shop_id, CreditConsent.revoked_at.is_(None))):
            row.revoked_at = now
        deleted = self.session.execute(delete(CreditProfileRecord).where(
            CreditProfileRecord.shop_id == self.shop_id)).rowcount
        self.session.flush()
        return deleted

    # ---- the profile

    def current_features(self) -> tuple[dict[str, float], int]:
        """Features over the books up to yesterday (complete days only), and the days of history."""
        yesterday = local_date(self.clock.now()) - timedelta(days=1)
        shop = ShopService(self.session, self.clock).get(self.shop_id)
        since = shop.data_since if shop else yesterday - timedelta(days=364)
        books = LedgerService(self.session, self.clock).book_days(self.shop_id, since, yesterday)
        try:
            return from_books(books), len(books)
        except ValueError as exc:
            raise NotEnoughHistory(str(exc)) from exc

    def build(self) -> CreditProfile:
        if self._active_consent() is None:
            raise ConsentRequired("The owner has not agreed to a credit profile yet.")
        if self.model is None:
            raise CreditModelMissing("The credit model is not trained. Run `python scripts/train_credit.py`.")
        features, history_days = self.current_features()
        x = vector(features, self.model.features)
        probability = float(self.model.probability(x)[0])
        contributions, _ = self.model.contributions(x)
        helps, hurts = explain.reasons(features, contributions, self.model.peers)
        band, words = band_for(probability)
        profile = CreditProfile(
            id=new_id("cred"), created_at=self.clock.now(), model_version=self.model.version,
            probability_of_default=round(probability, 4), band=band, band_words=words,
            summary=summary_text(probability, band, words), helps=helps, hurts=hurts,
            features=[FeatureView(name=n, label=BY_NAME[n].label, value=round(features[n], 4),
                                  typical=round(self.model.peers[n]["median"], 4), unit=BY_NAME[n].unit,
                                  scored=BY_NAME[n].scored) for n in NAMES],
            history_days=history_days,
        )
        self.session.add(CreditProfileRecord(id=profile.id, shop_id=self.shop_id, created_at=profile.created_at,
                                             model_version=profile.model_version, probability=probability, band=band,
                                             profile_json=profile.model_dump_json()))
        self.session.flush()
        return profile

    def latest(self) -> CreditProfile | None:
        if self._active_consent() is None:
            return None
        row = self.session.scalars(select(CreditProfileRecord).where(CreditProfileRecord.shop_id == self.shop_id)
                                   .order_by(CreditProfileRecord.created_at.desc())).first()
        return CreditProfile.model_validate_json(row.profile_json) if row else None

    def get(self, profile_id: str) -> CreditProfile | None:
        row = self.session.get(CreditProfileRecord, profile_id)
        if row is None or row.shop_id != self.shop_id or self._active_consent() is None:
            return None
        return CreditProfile.model_validate_json(row.profile_json)
