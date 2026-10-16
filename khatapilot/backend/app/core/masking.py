"""Phone-number normalisation and masking. Logs and the UI only ever show `017XXXX1234`."""

from __future__ import annotations

import re
from typing import Any

from app.core.money import normalize_digits

# Bangladeshi mobile numbers: 01[3-9] + 8 digits, optionally with +880 / 880, spaces or dashes.
PHONE_RE = re.compile(r"(?<!\d)(?:\+?880[\s-]?)?0?1[3-9](?:[\s-]?\d){8}(?!\d)")


def normalize_msisdn(raw: str) -> str | None:
    """Return the 11-digit local form (01XXXXXXXXX), or None if it is not a BD mobile number."""
    digits = re.sub(r"\D", "", normalize_digits(raw))
    if digits.startswith("880"):
        digits = digits[3:]
    if len(digits) == 10 and digits.startswith("1"):
        digits = "0" + digits
    if len(digits) == 11 and re.fullmatch(r"01[3-9]\d{8}", digits):
        return digits
    return None


def find_phone_numbers(text: str) -> list[str]:
    numbers = []
    for match in PHONE_RE.finditer(normalize_digits(text)):
        number = normalize_msisdn(match.group(0))
        if number:
            numbers.append(number)
    return numbers


def mask_phone(number: str) -> str:
    """01712345678 -> 017XXXX5678. Anything that is not a phone number is masked entirely."""
    normal = normalize_msisdn(number)
    if normal is None:
        return "X" * max(len(number), 4)
    return f"{normal[:3]}XXXX{normal[-4:]}"


def mask_number(number: str) -> str:
    """A phone number as 017XXXX5678; a bank account or anything else as ****1234 (enough to check it)."""
    return mask_phone(number) if normalize_msisdn(number) else f"****{number[-4:]}"


def mask_text(text: str) -> str:
    """Mask every phone number inside free text."""
    return PHONE_RE.sub(lambda m: mask_phone(m.group(0)), normalize_digits(text))


def mask_data(value: Any) -> Any:
    """Mask phone numbers in every string inside nested dicts and lists; numbers are left alone."""
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, dict):
        return {key: mask_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [mask_data(item) for item in value]
    return value
