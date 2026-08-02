"""Small, explicit data contracts shared across the core modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

OBJECT_KINDS = frozenset({"organisation", "property", "account", "asset", "document"})
SIGNAL_TYPES = frozenset(
    {
        "issuer_name",
        "property_address",
        "account_suffix",
        "merchant_name",
        "purchase_date",
        "amount_minor",
        "make",
        "model",
        "serial",
        "order_reference",
    }
)
EVIDENCE_CODES = frozenset(
    {
        "issuer_exact",
        "address_exact",
        "account_suffix_exact",
        "merchant_alias_exact",
        "model_exact",
        "serial_exact",
        "order_reference_exact",
    }
)
CANDIDATE_SPECIFIC_CODES = frozenset(
    {
        "address_exact",
        "account_suffix_exact",
        "model_exact",
        "serial_exact",
        "order_reference_exact",
    }
)
PREDICATES = frozenset({"concerns_property", "purchase_evidence_for"})


class EvidenceValidationError(ValueError):
    """A proposal contains evidence that cannot be recomputed."""


class PersistenceConflict(ValueError):
    """A defined persistence conflict safe to translate at an API boundary."""

    code = "persistence_conflict"


class IdempotencyConflict(PersistenceConflict):
    """An idempotency key or replay identity was reused for different semantics."""

    code = "idempotency_conflict"


class ProcessingConflict(PersistenceConflict):
    """A processing transition is invalid, expired, or owned by another worker."""

    code = "processing_conflict"


class ProposalConflict(PersistenceConflict):
    """Persisted proposals differ from a concurrent or replayed request."""

    code = "proposal_conflict"


@dataclass(frozen=True, slots=True)
class EvidencePlacement:
    sha256: str
    byte_size: int
    detected_mime: str
    relative_path: str


@dataclass(frozen=True, slots=True)
class SubmissionResult:
    document_id: str
    evidence_blob_id: str
    source_reference_id: str
    replayed_source: bool
    reused_document: bool


@dataclass(frozen=True, slots=True)
class ExtractionSignalInput:
    signal_type: str
    normalised_value: str
    display_value: str
    source_locator: str
    extractor: str
    extractor_version: str


@dataclass(frozen=True, slots=True)
class ProposalEvidenceInput:
    code: str
    signal_id: str
    matched_object_id: str
    validation_version: str = "1"


@dataclass(frozen=True, slots=True)
class ProposalInput:
    candidate_object_id: str
    predicate: str
    score: int
    evidence: tuple[ProposalEvidenceInput, ...]


@dataclass(frozen=True, slots=True)
class ProcessingProposalEvidenceInput:
    code: str
    signal_type: str
    normalised_value: str
    source_locator: str
    matched_object_id: str
    validation_version: str = "1"


@dataclass(frozen=True, slots=True)
class ProcessingProposalInput:
    candidate_object_id: str
    predicate: str
    score: int
    evidence: tuple[ProcessingProposalEvidenceInput, ...]


@dataclass(frozen=True, slots=True)
class ValidationRequest:
    document_id: str
    candidate_object_id: str
    predicate: str
    code: str
    signal_id: str
    matched_object_id: str


@runtime_checkable
class EvidenceValidator(Protocol):
    def validate(self, connection: object, request: ValidationRequest) -> bool:
        """Recompute one evidence claim using the transaction connection."""
        ...


@dataclass(frozen=True, slots=True)
class ConfirmationResult:
    decision_id: str
    relation_id: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class ProcessingCompletionResult:
    signal_ids: tuple[str, ...]
    proposal_ids: tuple[str, ...]
    replayed: bool


@dataclass(frozen=True, slots=True)
class RetryProcessingResult:
    document_id: str
    audit_event_id: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class RelatedDocument:
    document_id: str
    label: str
    document_type: str | None
    document_date: str | None
    predicate: str
    relation_id: str
    evidence: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    database_ok: bool
    foreign_key_errors: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    orphan_evidence: tuple[str, ...]
