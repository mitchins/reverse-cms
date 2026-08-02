"""Small immutable value objects shared by extraction and matching."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class SignalType(StrEnum):
    ISSUER_NAME = "issuer_name"
    PROPERTY_ADDRESS = "property_address"
    ACCOUNT_SUFFIX = "account_suffix"
    MERCHANT_NAME = "merchant_name"
    PURCHASE_DATE = "purchase_date"
    AMOUNT_MINOR = "amount_minor"
    MAKE = "make"
    MODEL = "model"
    SERIAL = "serial"
    ORDER_REFERENCE = "order_reference"


class EvidenceCode(StrEnum):
    ISSUER_EXACT = "issuer_exact"
    ADDRESS_EXACT = "address_exact"
    ACCOUNT_SUFFIX_EXACT = "account_suffix_exact"
    MERCHANT_ALIAS_EXACT = "merchant_alias_exact"
    MODEL_EXACT = "model_exact"
    SERIAL_EXACT = "serial_exact"
    ORDER_REFERENCE_EXACT = "order_reference_exact"


class DocumentKind(StrEnum):
    COUNCIL_RATES = "council_rates_notice"
    MORTGAGE_STATEMENT = "mortgage_statement"
    PURCHASE_RECEIPT = "purchase_receipt"


class RelationshipPredicate(StrEnum):
    CONCERNS_PROPERTY = "concerns_property"
    PURCHASE_EVIDENCE_FOR = "purchase_evidence_for"


@dataclass(frozen=True, slots=True)
class ExtractionSignal:
    signal_type: SignalType
    normalised_value: str
    display_value: str
    source_locator: str
    extractor: str = "anchor-v1"


@dataclass(frozen=True, slots=True)
class CandidateSnapshot:
    """Persisted candidate facts required to recompute evidence.

    Account and organisation values are projected by persistence into this
    snapshot. Ingestion cannot create or mutate any of these records.
    """

    object_id: str
    object_kind: str
    label: str
    address: str | None = None
    account_suffixes: tuple[str, ...] = ()
    issuer_names: tuple[str, ...] = ()
    merchant_aliases: tuple[str, ...] = ()
    make: str | None = None
    model: str | None = None
    serial: str | None = None
    order_reference: str | None = None
    matched_object_ids: dict[EvidenceCode, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EvidenceMatch:
    code: EvidenceCode
    signal: ExtractionSignal
    matched_object_id: str
    validation_version: str = "exact-v1"


@dataclass(frozen=True, slots=True)
class ProposalDraft:
    candidate: CandidateSnapshot
    predicate: RelationshipPredicate
    score: int
    rank: int
    evidence: tuple[EvidenceMatch, ...]
