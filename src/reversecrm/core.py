"""Application orchestration for the deterministic first vertical slice."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from sqlalchemy import func, select

from reversecrm.db import Database
from reversecrm.db.contracts import (
    ExtractionSignalInput,
    ProcessingProposalEvidenceInput,
    ProcessingProposalInput,
    ValidationRequest,
)
from reversecrm.db.schema import (
    audit_event,
    document,
    evidence_blob,
    extraction_signal,
    proposal,
    proposal_evidence,
    relation,
    review_decision,
    source_reference,
)
from reversecrm.db.services import ConfirmationService, PersistenceService
from reversecrm.domain.models import ExtractionSignal, ProposalDraft, SignalType
from reversecrm.evidence import EvidenceStore
from reversecrm.extract.anchors import EXTRACTOR_VERSION, extract_anchor
from reversecrm.ingest.pipeline import BoundedTextExtractor
from reversecrm.match.persistence import SqlCandidateReader, SqlEvidenceValidator
from reversecrm.match.service import build_proposals


@dataclass(frozen=True, slots=True)
class ProposalView:
    id: str
    target_id: str
    target_label: str
    predicate: str
    score: int
    rank: int
    evidence: tuple[tuple[str, str], ...]

    @property
    def evidence_codes(self) -> tuple[str, ...]:
        return tuple(code for code, _display in self.evidence)


@dataclass(frozen=True, slots=True)
class PersistenceCounts:
    source_references: int
    extraction_signals: int
    proposals: int
    relationships: int
    decisions: int
    audit_events: int


@dataclass(frozen=True, slots=True)
class SubmissionView:
    document_id: str
    blob_id: str
    proposals: tuple[ProposalView, ...]
    persistence_counts: PersistenceCounts
    created_subject_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ConfirmationView:
    decision_id: str
    relationship_id: str
    replayed: bool = field(compare=False)


@dataclass(frozen=True, slots=True)
class RelatedDocumentView:
    document_id: str
    label: str
    predicate: str
    relationship_id: str
    evidence: tuple[tuple[str, str], ...]

    @property
    def evidence_codes(self) -> tuple[str, ...]:
        return tuple(code for code, _display in self.evidence)


@dataclass(frozen=True, slots=True)
class SubmissionReceipt:
    document_id: str
    blob_id: str
    replayed_source: bool


@dataclass(frozen=True, slots=True)
class PreparedProcessing:
    """Pure processing output ready for one lease-verified persistence transaction."""

    document_type: str
    document_date: str | None
    extractor_version: str
    signals: tuple[ExtractionSignalInput, ...]
    proposals: tuple[ProcessingProposalInput, ...]


class CoreApplication:
    """Coordinate evidence placement, deterministic work, review, and query."""

    def __init__(self, database: Database, evidence_store: EvidenceStore) -> None:
        self.database = database
        self.evidence_store = evidence_store
        self.persistence = PersistenceService(database)
        self.confirmations = ConfirmationService(database)
        self.candidates = SqlCandidateReader(database)
        self.validator = SqlEvidenceValidator()

    def submit_and_process(
        self,
        *,
        content: bytes,
        filename: str,
        source_identity: str,
        text_extractor: BoundedTextExtractor | None = None,
    ) -> SubmissionView:
        submission = self.submit(
            content=content,
            filename=filename,
            source_identity=source_identity,
            max_bytes=(text_extractor or BoundedTextExtractor()).max_input_bytes,
        )
        state = self.persistence.get_processing_job(submission.document_id)
        if state is not None and state["state"] != "complete":
            worker_id = f"synchronous:{uuid.uuid4().hex}"
            claimed = self.persistence.claim_processing_job(worker_id=worker_id)
            if claimed != submission.document_id:
                raise RuntimeError("document processing job could not be claimed")
            prepared = self.prepare_processing(
                submission.document_id, text_extractor=text_extractor
            )
            self.persistence.complete_processing(
                submission.document_id,
                worker_id=worker_id,
                document_type=prepared.document_type,
                document_date=prepared.document_date,
                extractor_version=prepared.extractor_version,
                signals=prepared.signals,
                proposals=prepared.proposals,
                validator=self.validator,
            )
        return SubmissionView(
            submission.document_id,
            submission.blob_id,
            self.proposals(submission.document_id),
            self.counts(),
        )

    def submit(
        self,
        *,
        content: bytes,
        filename: str,
        source_identity: str,
        max_bytes: int,
    ) -> SubmissionReceipt:
        """Preserve evidence and enqueue its document; the worker processes it later."""

        mime = _detect_mime(content)
        placement = self.evidence_store.place(BytesIO(content), mime, max_bytes=max_bytes)
        submission = self.persistence.ingest_placement(
            source_kind="direct",
            source_identity=source_identity,
            placement=placement,
            original_name=Path(filename).name[:255],
        )
        return SubmissionReceipt(
            submission.document_id,
            submission.evidence_blob_id,
            submission.replayed_source,
        )

    def prepare_processing(
        self, document_id: str, *, text_extractor: BoundedTextExtractor | None = None
    ) -> PreparedProcessing:
        """Inspect, extract, and rank without opening a write transaction."""
        with self.database.connect() as connection:
            relative_path, mime = connection.execute(
                select(evidence_blob.c.relative_path, evidence_blob.c.detected_mime)
                .join(document, document.c.evidence_blob_id == evidence_blob.c.id)
                .where(document.c.object_id == document_id)
            ).one()
        text = (text_extractor or BoundedTextExtractor()).extract(
            self.evidence_store.resolve(relative_path), mime
        )
        result = extract_anchor(text)
        inputs = tuple(_signal_input(item) for item in result.signals)
        drafts = build_proposals(result.document_kind, result.signals, self.candidates)
        proposal_inputs = tuple(_processing_proposal_input(item) for item in drafts)
        purchase_date = next(
            (
                item.normalised_value
                for item in result.signals
                if item.signal_type is SignalType.PURCHASE_DATE
            ),
            None,
        )
        return PreparedProcessing(
            str(result.document_kind),
            purchase_date,
            EXTRACTOR_VERSION,
            inputs,
            proposal_inputs,
        )

    def proposals(self, document_id: str) -> tuple[ProposalView, ...]:
        rows = self.persistence.list_proposals(document_id)
        with self.database.connect() as connection:
            evidence: dict[str, tuple[tuple[str, str], ...]] = {}
            for item in rows:
                evidence[item["id"]] = tuple(
                    (row.evidence_code, row.display_value)
                    for row in connection.execute(
                        select(
                            proposal_evidence.c.evidence_code,
                            extraction_signal.c.display_value,
                        )
                        .join(
                            extraction_signal,
                            extraction_signal.c.id == proposal_evidence.c.extraction_signal_id,
                        )
                        .where(proposal_evidence.c.proposal_id == item["id"])
                        .order_by(proposal_evidence.c.evidence_code)
                    )
                )
        return tuple(
            ProposalView(
                item["id"],
                item["candidate_object_id"],
                item["label"],
                item["predicate"],
                item["score"],
                item["rank"],
                evidence[item["id"]],
            )
            for item in rows
        )

    def all_evidence_revalidates(self, document_id: str) -> bool:
        with self.database.connect() as connection:
            rows = connection.execute(
                select(
                    proposal.c.candidate_object_id,
                    proposal.c.predicate,
                    proposal_evidence.c.evidence_code,
                    proposal_evidence.c.extraction_signal_id,
                    proposal_evidence.c.matched_object_id,
                )
                .join(proposal_evidence, proposal_evidence.c.proposal_id == proposal.c.id)
                .where(proposal.c.document_id == document_id)
            )
            return all(
                self.validator.validate(
                    connection,
                    ValidationRequest(
                        document_id,
                        row.candidate_object_id,
                        row.predicate,
                        row.evidence_code,
                        row.extraction_signal_id,
                        row.matched_object_id,
                    ),
                )
                for row in rows
            )

    def confirm(self, proposal_id: str, reviewer: str, idempotency_key: str) -> ConfirmationView:
        result = self.confirmations.confirm(
            proposal_id=proposal_id,
            reviewer=reviewer,
            idempotency_key=idempotency_key,
            validator=self.validator,
        )
        return ConfirmationView(result.decision_id, result.relation_id, result.replayed)

    def related_documents(self, object_id: str) -> tuple[RelatedDocumentView, ...]:
        return tuple(
            RelatedDocumentView(
                item.document_id,
                item.label,
                item.predicate,
                item.relation_id,
                item.evidence,
            )
            for item in self.persistence.query_related_documents(object_id)
        )

    def counts(self) -> PersistenceCounts:
        tables = (
            source_reference,
            extraction_signal,
            proposal,
            relation,
            review_decision,
            audit_event,
        )
        with self.database.connect() as connection:
            values = [
                connection.execute(select(func.count()).select_from(table)).scalar_one()
                for table in tables
            ]
        return PersistenceCounts(*values)


def _signal_input(signal: ExtractionSignal) -> ExtractionSignalInput:
    return ExtractionSignalInput(
        str(signal.signal_type),
        signal.normalised_value,
        signal.display_value,
        signal.source_locator,
        "anchor",
        EXTRACTOR_VERSION,
    )


def _processing_proposal_input(draft: ProposalDraft) -> ProcessingProposalInput:
    evidence = tuple(
        ProcessingProposalEvidenceInput(
            str(item.code),
            str(item.signal.signal_type),
            item.signal.normalised_value,
            item.signal.source_locator,
            item.matched_object_id,
            item.validation_version,
        )
        for item in draft.evidence
    )
    return ProcessingProposalInput(
        draft.candidate.object_id,
        str(draft.predicate),
        draft.score,
        evidence,
    )


def _detect_mime(content: bytes) -> str:
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    raise ValueError("unsupported document content")
