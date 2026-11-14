from __future__ import annotations

from datetime import datetime

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base


class BufferSetting(Base):
    """A safety buffer the owner chose themself. No row: the buffer is sized from the shop's own books."""

    __tablename__ = "forecast_buffer_settings"

    shop_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    buffer_paisa: Mapped[int]
    set_at: Mapped[datetime]
