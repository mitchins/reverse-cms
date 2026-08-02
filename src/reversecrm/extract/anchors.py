"""Line-oriented, deterministic extraction for the three anchor families."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from reversecrm.domain.models import DocumentKind, ExtractionSignal, SignalType
from reversecrm.domain.normalise import (
    amount_to_minor,
    normalise_account_suffix,
    normalise_identifier,
    normalise_text,
)

EXTRACTOR_VERSION = "anchor-v1"
_LABEL = re.compile(r"^\s*([A-Za-z][A-Za-z /-]{1,40})\s*:\s*(.*?)\s*$")
_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %B %Y", "%d %b %Y")


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    document_kind: DocumentKind
    signals: tuple[ExtractionSignal, ...]


def _fields(text: str) -> dict[str, list[tuple[str, str]]]:
    result: dict[str, list[tuple[str, str]]] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        match = _LABEL.match(line)
        if not match or not match.group(2):
            continue
        key = normalise_text(match.group(1))
        result.setdefault(key, []).append((match.group(2).strip(), f"line:{number}"))
    return result


def _first(fields: dict[str, list[tuple[str, str]]], *labels: str) -> tuple[str, str] | None:
    for label in labels:
        values = fields.get(label)
        if values:
            return values[0]
    return None


def _signal(signal_type: SignalType, raw: tuple[str, str], normalised: str) -> ExtractionSignal:
    value, locator = raw
    if not normalised:
        raise ValueError(f"empty {signal_type} signal")
    return ExtractionSignal(signal_type, normalised, value[:160], locator, EXTRACTOR_VERSION)


def _normalise_date(value: str) -> str:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError("unsupported date format")


def _classify(text: str, fields: dict[str, list[tuple[str, str]]]) -> DocumentKind:
    headings = normalise_text(" ".join(text.splitlines()[:8]))
    keys = set(fields)
    if "COUNCIL RATES" in headings or "ASSESSMENT NUMBER" in keys or "RATES ACCOUNT" in keys:
        return DocumentKind.COUNCIL_RATES
    if "MORTGAGE STATEMENT" in headings or "LOAN ACCOUNT" in keys or "MORTGAGED PROPERTY" in keys:
        return DocumentKind.MORTGAGE_STATEMENT
    if "RECEIPT" in headings or "TAX INVOICE" in headings or {"MODEL", "SERIAL"} <= keys:
        return DocumentKind.PURCHASE_RECEIPT
    raise ValueError("document does not match a registered deterministic extractor")


def _heading_identity(text: str) -> tuple[str, str] | None:
    """Return a conventional letterhead line immediately before the heading."""

    lines = [
        (line.strip(), f"line:{number}")
        for number, line in enumerate(text.splitlines(), 1)
        if line.strip()
    ]
    if len(lines) < 2:
        return None
    heading = normalise_text(lines[1][0])
    markers = ("COUNCIL RATES", "MORTGAGE STATEMENT", "PURCHASE RECEIPT", "TAX INVOICE")
    return lines[0] if any(marker in heading for marker in markers) else None


def _add_signal(
    signals: list[ExtractionSignal],
    fields: dict[str, list[tuple[str, str]]],
    signal_type: SignalType,
    normaliser: Callable[[str], str],
    *labels: str,
) -> None:
    raw = _first(fields, *labels)
    if raw is not None:
        signals.append(_signal(signal_type, raw, normaliser(raw[0])))


def _property_signals(
    fields: dict[str, list[tuple[str, str]]], letterhead: tuple[str, str] | None
) -> list[ExtractionSignal]:
    signals: list[ExtractionSignal] = []
    _add_signal(
        signals, fields, SignalType.ISSUER_NAME, normalise_text, "ISSUER", "LENDER", "COUNCIL"
    )
    if not signals and letterhead is not None:
        signals.append(_signal(SignalType.ISSUER_NAME, letterhead, normalise_text(letterhead[0])))
    _add_signal(
        signals,
        fields,
        SignalType.PROPERTY_ADDRESS,
        normalise_text,
        "PROPERTY ADDRESS",
        "SERVICE ADDRESS",
        "SERVICE PROPERTY",
        "MORTGAGED PROPERTY",
    )
    _add_signal(
        signals,
        fields,
        SignalType.ACCOUNT_SUFFIX,
        normalise_account_suffix,
        "ACCOUNT SUFFIX",
        "ACCOUNT NUMBER",
        "LOAN ACCOUNT",
        "MORTGAGE ACCOUNT",
        "ASSESSMENT NUMBER",
        "ASSESSMENT ACCOUNT",
    )
    return signals


def _asset_signals(
    fields: dict[str, list[tuple[str, str]]], letterhead: tuple[str, str] | None
) -> list[ExtractionSignal]:
    signals: list[ExtractionSignal] = []
    _add_signal(
        signals, fields, SignalType.MERCHANT_NAME, normalise_text, "MERCHANT", "SELLER", "STORE"
    )
    if not signals and letterhead is not None:
        signals.append(_signal(SignalType.MERCHANT_NAME, letterhead, normalise_text(letterhead[0])))
    _add_signal(signals, fields, SignalType.PURCHASE_DATE, _normalise_date, "PURCHASE DATE", "DATE")
    _add_signal(
        signals, fields, SignalType.AMOUNT_MINOR, amount_to_minor, "AMOUNT", "TOTAL", "TOTAL AMOUNT"
    )
    _add_signal(signals, fields, SignalType.MAKE, normalise_text, "MAKE", "BRAND")
    _add_signal(signals, fields, SignalType.MODEL, normalise_identifier, "MODEL")
    item = _first(fields, "ITEM", "PRODUCT")
    if item is not None and len(tokens := item[0].split()) >= 2:
        if not any(signal.signal_type is SignalType.MAKE for signal in signals):
            signals.append(
                _signal(SignalType.MAKE, (tokens[0], item[1]), normalise_text(tokens[0]))
            )
        if not any(signal.signal_type is SignalType.MODEL for signal in signals):
            signals.append(
                _signal(SignalType.MODEL, (tokens[1], item[1]), normalise_identifier(tokens[1]))
            )
    _add_signal(signals, fields, SignalType.SERIAL, normalise_identifier, "SERIAL", "SERIAL NUMBER")
    _add_signal(
        signals,
        fields,
        SignalType.ORDER_REFERENCE,
        normalise_identifier,
        "ORDER REFERENCE",
        "ORDER NUMBER",
    )
    return signals


def extract_anchor(text: str) -> ExtractionResult:
    """Extract only explicit labelled values; no inference or model fallback."""

    if len(text.encode("utf-8")) > 2_000_000:
        raise ValueError("OCR text exceeds extraction limit")
    fields = _fields(text)
    kind = _classify(text, fields)
    letterhead = _heading_identity(text)
    if kind in {DocumentKind.COUNCIL_RATES, DocumentKind.MORTGAGE_STATEMENT}:
        signals = _property_signals(fields, letterhead)
    else:
        signals = _asset_signals(fields, letterhead)
    if not signals:
        raise ValueError("registered document yielded no explicit signals")
    return ExtractionResult(kind, tuple(signals))
