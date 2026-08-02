"""Conservative normalisation used by extractors and exact validators."""

from __future__ import annotations

import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_SPACE = re.compile(r"\s+")
_PUNCTUATION = re.compile(r"[^a-z0-9 ]+")


def normalise_text(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return _SPACE.sub(" ", _PUNCTUATION.sub(" ", ascii_value.casefold())).strip().upper()


def normalise_identifier(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9-]", "", value).upper()


def normalise_account_suffix(value: str) -> str:
    identifier = re.sub(r"[^A-Za-z0-9]", "", value).upper()
    return identifier[-4:]


def amount_to_minor(value: str) -> str:
    cleaned = value.replace("$", "").replace(",", "").strip()
    try:
        decimal = Decimal(cleaned).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError("invalid monetary amount") from exc
    return str(int(decimal * 100))
