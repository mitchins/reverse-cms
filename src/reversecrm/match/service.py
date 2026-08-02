"""Exact candidate retrieval, evidence validation, and stable ranking."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from reversecrm.domain.evidence import CANDIDATE_SPECIFIC_CODES, validate_evidence
from reversecrm.domain.models import (
    CandidateSnapshot,
    DocumentKind,
    EvidenceCode,
    EvidenceMatch,
    ExtractionSignal,
    ProposalDraft,
    RelationshipPredicate,
    SignalType,
)

MAX_CANDIDATES = 3


class CandidateReader(Protocol):
    """Persistence projection; implementations must use indexed exact queries."""

    def find_property_candidates(
        self, signals: Mapping[SignalType, tuple[str, ...]], *, limit: int
    ) -> Sequence[CandidateSnapshot]: ...

    def find_asset_candidates(
        self, signals: Mapping[SignalType, tuple[str, ...]], *, limit: int
    ) -> Sequence[CandidateSnapshot]: ...


_CODE_BY_SIGNAL = {
    SignalType.ISSUER_NAME: EvidenceCode.ISSUER_EXACT,
    SignalType.PROPERTY_ADDRESS: EvidenceCode.ADDRESS_EXACT,
    SignalType.ACCOUNT_SUFFIX: EvidenceCode.ACCOUNT_SUFFIX_EXACT,
    SignalType.MERCHANT_NAME: EvidenceCode.MERCHANT_ALIAS_EXACT,
    SignalType.MODEL: EvidenceCode.MODEL_EXACT,
    SignalType.SERIAL: EvidenceCode.SERIAL_EXACT,
    SignalType.ORDER_REFERENCE: EvidenceCode.ORDER_REFERENCE_EXACT,
}
_WEIGHT = {
    EvidenceCode.ISSUER_EXACT: 20,
    EvidenceCode.ADDRESS_EXACT: 60,
    EvidenceCode.ACCOUNT_SUFFIX_EXACT: 50,
    EvidenceCode.MERCHANT_ALIAS_EXACT: 15,
    EvidenceCode.MODEL_EXACT: 45,
    EvidenceCode.SERIAL_EXACT: 70,
    EvidenceCode.ORDER_REFERENCE_EXACT: 60,
}


def _group(signals: Sequence[ExtractionSignal]) -> dict[SignalType, tuple[str, ...]]:
    grouped: dict[SignalType, list[str]] = {}
    for signal in signals:
        grouped.setdefault(signal.signal_type, []).append(signal.normalised_value)
    return {key: tuple(dict.fromkeys(values)) for key, values in grouped.items()}


def _validated_matches(
    signals: Sequence[ExtractionSignal], candidate: CandidateSnapshot
) -> tuple[EvidenceMatch, ...]:
    matches: list[EvidenceMatch] = []
    for signal in signals:
        code = _CODE_BY_SIGNAL.get(signal.signal_type)
        if code is None:
            continue
        try:
            matches.append(validate_evidence(code, signal, candidate))
        except ValueError:
            continue
    # One row per evidence code is enough and produces deterministic provenance.
    by_code = {match.code: match for match in matches}
    return tuple(by_code[code] for code in sorted(by_code, key=str))


def build_proposals(
    kind: DocumentKind,
    signals: Sequence[ExtractionSignal],
    candidates: CandidateReader,
) -> tuple[ProposalDraft, ...]:
    """Retrieve and rank no more than three existing candidates."""

    grouped = _group(signals)
    if kind in {DocumentKind.COUNCIL_RATES, DocumentKind.MORTGAGE_STATEMENT}:
        snapshots = candidates.find_property_candidates(grouped, limit=MAX_CANDIDATES)
        predicate = RelationshipPredicate.CONCERNS_PROPERTY
    else:
        snapshots = candidates.find_asset_candidates(grouped, limit=MAX_CANDIDATES)
        predicate = RelationshipPredicate.PURCHASE_EVIDENCE_FOR
    if len(snapshots) > MAX_CANDIDATES:
        raise ValueError("candidate reader exceeded the bounded retrieval contract")

    scored: list[tuple[int, CandidateSnapshot, tuple[EvidenceMatch, ...]]] = []
    seen: set[str] = set()
    for candidate in snapshots:
        if candidate.object_id in seen:
            continue
        seen.add(candidate.object_id)
        evidence = _validated_matches(signals, candidate)
        if not any(item.code in CANDIDATE_SPECIFIC_CODES for item in evidence):
            continue
        score = sum(_WEIGHT[item.code] for item in evidence)
        scored.append((score, candidate, evidence))
    scored.sort(key=lambda item: (-item[0], item[1].object_id))
    return tuple(
        ProposalDraft(candidate, predicate, score, rank, evidence)
        for rank, (score, candidate, evidence) in enumerate(scored[:MAX_CANDIDATES], start=1)
    )
