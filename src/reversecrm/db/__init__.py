"""SQLite persistence contracts for the reverse-CRM core."""

from .connection import Database
from .contracts import (
    ConfirmationResult,
    EvidenceValidationError,
    ExtractionSignalInput,
    IdempotencyConflict,
    PersistenceConflict,
    ProcessingCompletionResult,
    ProcessingConflict,
    ProcessingProposalEvidenceInput,
    ProcessingProposalInput,
    ProposalConflict,
    ProposalEvidenceInput,
    ProposalInput,
    RelatedDocument,
    RetryProcessingResult,
    SubmissionResult,
    ValidationRequest,
)
from .services import ConfirmationService, PersistenceService

__all__ = [
    "ConfirmationResult",
    "ConfirmationService",
    "Database",
    "EvidenceValidationError",
    "ExtractionSignalInput",
    "IdempotencyConflict",
    "PersistenceConflict",
    "PersistenceService",
    "ProcessingCompletionResult",
    "ProcessingConflict",
    "ProcessingProposalEvidenceInput",
    "ProcessingProposalInput",
    "ProposalConflict",
    "ProposalEvidenceInput",
    "ProposalInput",
    "RelatedDocument",
    "RetryProcessingResult",
    "SubmissionResult",
    "ValidationRequest",
]
