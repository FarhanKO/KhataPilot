"""Money is stored as integer paisa (1 taka = 100 paisa) so arithmetic is exact.

Parsing accepts the forms shop owners and bKash/Nagad SMS actually use:
`৳1,500`, `Tk 1,500.00`, `BDT 1500`, `1500 taka`, `8k`, `8 hajar`, Bangla digits (০–৯) and lakh.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

BANGLA_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")

_MULTIPLIERS = {
    "k": 1_000,
    "hajar": 1_000,
    "hazar": 1_000,
    "হাজার": 1_000,
    "thousand": 1_000,
    "lakh": 100_000,
    "lac": 100_000,
    "lakhs": 100_000,
    "লাখ": 100_000,
}

# A number such as 1,500 / 1500.50 / ১৫০০ / 12,50,000 followed by an optional multiplier word.
AMOUNT_RE = re.compile(
    r"(?P<num>\d{1,3}(?:,\d{2,3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"
    r"\s*(?P<mult>k|hajar|hazar|হাজার|thousand|lakhs?|lac|লাখ)?(?![a-z])",
    re.IGNORECASE,
)


def normalize_digits(text: str) -> str:
    return text.translate(BANGLA_DIGITS)


def to_paisa(value: Decimal | int | float | str) -> int:
    try:
        amount = Decimal(normalize_digits(str(value)).replace(",", "").strip())
    except InvalidOperation as exc:
        raise ValueError(f"not an amount: {value!r}") from exc
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def taka(value: Decimal | int | float | str) -> int:
    """Readable alias: `taka(8000)` is ৳8,000 expressed in paisa."""
    return to_paisa(value)


def from_paisa(paisa: int) -> Decimal:
    return (Decimal(paisa) / 100).quantize(Decimal("0.01"))


def fmt_taka(paisa: int) -> str:
    """`৳12,500` for whole taka, `৳1,500.50` otherwise, `-৳2,000` for negatives."""
    sign = "-" if paisa < 0 else ""
    whole, frac = divmod(abs(paisa), 100)
    body = f"{whole:,}" if frac == 0 else f"{whole:,}.{frac:02d}"
    return f"{sign}৳{body}"


def parse_amount(token: str) -> int | None:
    """Parse one amount expression ("8,000", "8k", "৮ হাজার", "1.5 lakh") into paisa."""
    match = AMOUNT_RE.fullmatch(normalize_digits(token).strip().lower())
    if not match:
        return None
    return _match_to_paisa(match)


def find_amounts(text: str) -> list[int]:
    """Every amount mentioned in free text, in order, as paisa.

    Bare numbers that look like phone numbers (10+ digits) or years are skipped.
    """
    found: list[int] = []
    for match in AMOUNT_RE.finditer(normalize_digits(text).lower()):
        digits = match.group("num").replace(",", "").split(".")[0]
        if len(digits) >= 10:
            continue
        value = _match_to_paisa(match)
        if value is not None and value > 0:
            found.append(value)
    return found


def _match_to_paisa(match: re.Match[str]) -> int | None:
    number = Decimal(match.group("num").replace(",", ""))
    mult = match.group("mult")
    if mult:
        number *= _MULTIPLIERS[mult.lower()]
    return to_paisa(number)
