"""Closed evidence registry; callers cannot manufacture evidence claims."""

from __future__ import annotations

from collections.abc import Callable

from .models import CandidateSnapshot, EvidenceCode, EvidenceMatch, ExtractionSignal, SignalType

Validator = Callable[[ExtractionSignal, CandidateSnapshot], bool]


def _equals(value: str | None, signal: ExtractionSignal) -> bool:
    return value is not None and value == signal.normalised_value


def _in(values: tuple[str, ...], signal: ExtractionSignal) -> bool:
    return signal.normalised_value in values


_REGISTRY: dict[EvidenceCode, tuple[SignalType, Validator]] = {
    EvidenceCode.ISSUER_EXACT: (
        SignalType.ISSUER_NAME,
        lambda signal, candidate: _in(candidate.issuer_names, signal),
    ),
    EvidenceCode.ADDRESS_EXACT: (
        SignalType.PROPERTY_ADDRESS,
        lambda signal, candidate: _equals(candidate.address, signal),
    ),
    EvidenceCode.ACCOUNT_SUFFIX_EXACT: (
        SignalType.ACCOUNT_SUFFIX,
        lambda signal, candidate: _in(candidate.account_suffixes, signal),
    ),
    EvidenceCode.MERCHANT_ALIAS_EXACT: (
        SignalType.MERCHANT_NAME,
        lambda signal, candidate: _in(candidate.merchant_aliases, signal),
    ),
    EvidenceCode.MODEL_EXACT: (
        SignalType.MODEL,
        lambda signal, candidate: _equals(candidate.model, signal),
    ),
    EvidenceCode.SERIAL_EXACT: (
        SignalType.SERIAL,
        lambda signal, candidate: _equals(candidate.serial, signal),
    ),
    EvidenceCode.ORDER_REFERENCE_EXACT: (
        SignalType.ORDER_REFERENCE,
        lambda signal, candidate: _equals(candidate.order_reference, signal),
    ),
}

CANDIDATE_SPECIFIC_CODES = frozenset(
    {
        EvidenceCode.ADDRESS_EXACT,
        EvidenceCode.ACCOUNT_SUFFIX_EXACT,
        EvidenceCode.MODEL_EXACT,
        EvidenceCode.SERIAL_EXACT,
        EvidenceCode.ORDER_REFERENCE_EXACT,
    }
)


def validate_evidence(
    code: EvidenceCode,
    signal: ExtractionSignal,
    candidate: CandidateSnapshot,
) -> EvidenceMatch:
    """Recompute a code from a stored signal and persisted candidate facts."""

    try:
        required_signal, validator = _REGISTRY[code]
    except KeyError as exc:  # defensive if an untyped persistence value enters
        raise ValueError(f"unknown evidence code: {code}") from exc
    if signal.signal_type is not required_signal or not validator(signal, candidate):
        raise ValueError(f"evidence {code} is not supported by the supplied signal")
    matched_object = candidate.matched_object_ids.get(code, candidate.object_id)
    return EvidenceMatch(code=code, signal=signal, matched_object_id=matched_object)


def registry_codes() -> frozenset[EvidenceCode]:
    return frozenset(_REGISTRY)
