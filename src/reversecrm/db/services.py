"""Explicit persistence/query services for the first vertical slice."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, insert, or_, select, update

from .connection import Database
from .contracts import (
    CANDIDATE_SPECIFIC_CODES,
    EVIDENCE_CODES,
    PREDICATES,
    SIGNAL_TYPES,
    ConfirmationResult,
    EvidencePlacement,
    EvidenceValidationError,
    EvidenceValidator,
    ExtractionSignalInput,
    IdempotencyConflict,
    ProcessingCompletionResult,
    ProcessingConflict,
    ProcessingProposalInput,
    ProposalConflict,
    ProposalEvidenceInput,
    ProposalInput,
    RelatedDocument,
    RetryProcessingResult,
    SubmissionResult,
    ValidationRequest,
)
from .schema import (
    account,
    asset,
    audit_event,
    document,
    evidence_blob,
    evidence_link,
    extraction_signal,
    object_table,
    organisation,
    organisation_alias,
    processing_job,
    property_table,
    proposal,
    proposal_evidence,
    relation,
    review_decision,
    source_reference,
)


def _id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(UTC).isoformat()


class PersistenceService:
    def __init__(self, database: Database) -> None:
        self.database = database

    # Subject creation is an administrative/fixture operation, never called by ingestion.
    def create_organisation(
        self, label: str, normalised_name: str, aliases: Iterable[str] = ()
    ) -> str:
        object_id = _id()
        now = _now()
        with self.database.transaction() as connection:
            connection.execute(
                insert(object_table).values(
                    id=object_id, kind="organisation", label=label, created_at=now, updated_at=now
                )
            )
            connection.execute(
                insert(organisation).values(
                    object_id=object_id, canonical_name=label, normalised_name=normalised_name
                )
            )
            for alias in sorted(set(aliases)):
                connection.execute(
                    insert(organisation_alias).values(
                        organisation_id=object_id, normalised_alias=alias
                    )
                )
        return object_id

    def create_property(self, label: str, normalised_address: str, occupancy: str | None) -> str:
        object_id = _id()
        now = _now()
        with self.database.transaction() as connection:
            connection.execute(
                insert(object_table).values(
                    id=object_id, kind="property", label=label, created_at=now, updated_at=now
                )
            )
            connection.execute(
                insert(property_table).values(
                    object_id=object_id, normalised_address=normalised_address, occupancy=occupancy
                )
            )
        return object_id

    def create_account(
        self, label: str, account_type: str, suffix: str, issuer_id: str, related_subject_id: str
    ) -> str:
        object_id = _id()
        now = _now()
        with self.database.transaction() as connection:
            connection.execute(
                insert(object_table).values(
                    id=object_id, kind="account", label=label, created_at=now, updated_at=now
                )
            )
            connection.execute(
                insert(account).values(
                    object_id=object_id,
                    account_type=account_type,
                    suffix=suffix,
                    issuer_organisation_id=issuer_id,
                    related_subject_id=related_subject_id,
                )
            )
        return object_id

    def create_asset(
        self,
        label: str,
        *,
        make: str | None = None,
        model: str | None = None,
        serial: str | None = None,
        order_reference: str | None = None,
    ) -> str:
        object_id = _id()
        now = _now()
        with self.database.transaction() as connection:
            connection.execute(
                insert(object_table).values(
                    id=object_id, kind="asset", label=label, created_at=now, updated_at=now
                )
            )
            connection.execute(
                insert(asset).values(
                    object_id=object_id,
                    make=make,
                    model=model,
                    serial=serial,
                    order_reference=order_reference,
                )
            )
        return object_id

    def list_subjects(self, kind: str) -> list[dict[str, Any]]:
        if kind not in {"organisation", "property", "account", "asset"}:
            raise ValueError("unsupported subject kind")
        detail = {
            "organisation": organisation,
            "property": property_table,
            "account": account,
            "asset": asset,
        }[kind]
        with self.database.connect() as connection:
            rows = connection.execute(
                select(object_table, detail)
                .join(detail, detail.c.object_id == object_table.c.id)
                .where(object_table.c.kind == kind)
                .order_by(object_table.c.id)
            ).mappings()
            return [dict(row) for row in rows]

    def list_organisation_aliases(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    select(organisation_alias).order_by(
                        organisation_alias.c.organisation_id, organisation_alias.c.normalised_alias
                    )
                ).mappings()
            ]

    def ingest_placement(
        self,
        *,
        source_kind: str,
        source_identity: str,
        placement: EvidencePlacement,
        original_name: str | None = None,
        received_metadata: Mapping[str, Any] | None = None,
    ) -> SubmissionResult:
        """Register pre-hashed evidence and its logical document atomically."""
        if not source_kind.strip() or not source_identity.strip():
            raise ValueError("source kind and identity must be non-empty")
        if (
            len(placement.sha256) != 64
            or any(character not in "0123456789abcdef" for character in placement.sha256)
            or placement.relative_path
            != f"{placement.sha256[:2]}/{placement.sha256[2:4]}/{placement.sha256}"
            or placement.byte_size < 0
            or not placement.detected_mime
        ):
            raise ValueError("invalid evidence placement")
        metadata_json = json.dumps(received_metadata or {}, sort_keys=True, separators=(",", ":"))
        with self.database.transaction() as connection:
            replay = (
                connection.execute(
                    select(source_reference).where(
                        and_(
                            source_reference.c.source_kind == source_kind,
                            source_reference.c.source_identity == source_identity,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if replay is not None:
                blob = (
                    connection.execute(
                        select(evidence_blob).where(
                            evidence_blob.c.id == replay["evidence_blob_id"]
                        )
                    )
                    .mappings()
                    .one()
                )
                if blob["sha256"] != placement.sha256:
                    raise IdempotencyConflict(
                        "source identity was already registered with different evidence"
                    )
                document_id = connection.execute(
                    select(document.c.object_id).where(document.c.evidence_blob_id == blob["id"])
                ).scalar_one()
                return SubmissionResult(document_id, blob["id"], replay["id"], True, True)

            existing_blob = (
                connection.execute(
                    select(evidence_blob).where(evidence_blob.c.sha256 == placement.sha256)
                )
                .mappings()
                .one_or_none()
            )
            if existing_blob is None:
                blob_id = _id()
                connection.execute(
                    insert(evidence_blob).values(
                        id=blob_id,
                        sha256=placement.sha256,
                        byte_size=placement.byte_size,
                        detected_mime=placement.detected_mime,
                        relative_path=placement.relative_path,
                        created_at=_now(),
                    )
                )
            else:
                blob_id = existing_blob["id"]
                if (
                    existing_blob["byte_size"] != placement.byte_size
                    or existing_blob["relative_path"] != placement.relative_path
                    or existing_blob["detected_mime"] != placement.detected_mime
                ):
                    raise ValueError("evidence metadata conflicts with registered digest")

            existing_document = connection.execute(
                select(document.c.object_id).where(document.c.evidence_blob_id == blob_id)
            ).scalar_one_or_none()
            reused_document = existing_document is not None
            if existing_document is None:
                document_id = _id()
                now = _now()
                connection.execute(
                    insert(object_table).values(
                        id=document_id,
                        kind="document",
                        label=original_name or f"Document {placement.sha256[:12]}",
                        created_at=now,
                        updated_at=now,
                    )
                )
                connection.execute(
                    insert(document).values(
                        object_id=document_id, evidence_blob_id=blob_id, processing_state="pending"
                    )
                )
                connection.execute(
                    insert(processing_job).values(
                        document_id=document_id, state="pending", attempt_count=0, updated_at=now
                    )
                )
            else:
                document_id = existing_document
            source_id = _id()
            connection.execute(
                insert(source_reference).values(
                    id=source_id,
                    source_kind=source_kind,
                    source_identity=source_identity,
                    evidence_blob_id=blob_id,
                    original_name=original_name,
                    received_metadata=metadata_json,
                    received_at=_now(),
                )
            )
            return SubmissionResult(document_id, blob_id, source_id, False, reused_document)

    def store_extraction_signals(
        self, document_id: str, signals: Iterable[ExtractionSignalInput]
    ) -> list[str]:
        """Insert signals idempotently and return IDs in input order."""
        with self.database.transaction() as connection:
            return self._store_extraction_signals(connection, document_id, tuple(signals))

    @staticmethod
    def _store_extraction_signals(
        connection: Any, document_id: str, signals: Sequence[ExtractionSignalInput]
    ) -> list[str]:
        result: list[str] = []
        for signal in signals:
            if signal.signal_type not in SIGNAL_TYPES:
                raise ValueError(f"unknown extraction signal type: {signal.signal_type}")
            identity = and_(
                extraction_signal.c.document_id == document_id,
                extraction_signal.c.signal_type == signal.signal_type,
                extraction_signal.c.normalised_value == signal.normalised_value,
                extraction_signal.c.source_locator == signal.source_locator,
            )
            existing = (
                connection.execute(select(extraction_signal).where(identity))
                .mappings()
                .one_or_none()
            )
            if existing is None:
                signal_id = _id()
                connection.execute(
                    insert(extraction_signal).values(
                        id=signal_id,
                        document_id=document_id,
                        signal_type=signal.signal_type,
                        normalised_value=signal.normalised_value,
                        display_value=signal.display_value,
                        source_locator=signal.source_locator,
                        extractor=signal.extractor,
                        extractor_version=signal.extractor_version,
                    )
                )
            else:
                signal_id = existing["id"]
                stored_semantics = (
                    existing["display_value"],
                    existing["extractor"],
                    existing["extractor_version"],
                )
                requested_semantics = (
                    signal.display_value,
                    signal.extractor,
                    signal.extractor_version,
                )
                if stored_semantics != requested_semantics:
                    raise ProcessingConflict(
                        "stored extraction signal differs from replayed extraction"
                    )
            result.append(signal_id)
        return result

    def list_extraction_signals(self, document_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    select(extraction_signal)
                    .where(extraction_signal.c.document_id == document_id)
                    .order_by(extraction_signal.c.id)
                ).mappings()
            ]

    def persist_proposals(
        self, document_id: str, proposals: Sequence[ProposalInput], validator: EvidenceValidator
    ) -> list[str]:
        with self.database.transaction() as connection:
            ids, _replayed = self._persist_proposals(connection, document_id, proposals, validator)
            return ids

    @staticmethod
    def _persist_proposals(
        connection: Any,
        document_id: str,
        proposals: Sequence[ProposalInput],
        validator: EvidenceValidator,
    ) -> tuple[list[str], bool]:
        if len(proposals) > 3:
            raise ValueError("at most three proposals may be persisted")
        identities = {(item.candidate_object_id, item.predicate) for item in proposals}
        if len(identities) != len(proposals):
            raise ValueError("duplicate proposal candidate/predicate")
        existing = list(
            connection.execute(
                select(proposal)
                .where(proposal.c.document_id == document_id)
                .order_by(proposal.c.rank)
            ).mappings()
        )
        if existing:
            if len(existing) != len(proposals):
                raise ProposalConflict("persisted proposals differ from replay")
            for rank, (row, item) in enumerate(zip(existing, proposals, strict=True), start=1):
                if (
                    row["candidate_object_id"],
                    row["predicate"],
                    row["score"],
                    row["rank"],
                ) != (item.candidate_object_id, item.predicate, item.score, rank):
                    raise ProposalConflict("persisted proposals differ from replay")
                stored_evidence = set(
                    connection.execute(
                        select(
                            proposal_evidence.c.evidence_code,
                            proposal_evidence.c.extraction_signal_id,
                            proposal_evidence.c.matched_object_id,
                            proposal_evidence.c.validation_version,
                        ).where(proposal_evidence.c.proposal_id == row["id"])
                    ).tuples()
                )
                requested_evidence = {
                    (
                        evidence.code,
                        evidence.signal_id,
                        evidence.matched_object_id,
                        evidence.validation_version,
                    )
                    for evidence in item.evidence
                }
                if stored_evidence != requested_evidence:
                    raise ProposalConflict("persisted proposal evidence differs from replay")
                for evidence in item.evidence:
                    request = ValidationRequest(
                        document_id,
                        item.candidate_object_id,
                        item.predicate,
                        evidence.code,
                        evidence.signal_id,
                        evidence.matched_object_id,
                    )
                    if not validator.validate(connection, request):
                        raise EvidenceValidationError(
                            f"evidence failed validation: {evidence.code}"
                        )
            return [row["id"] for row in existing], True

        ids: list[str] = []
        for rank, item in enumerate(proposals, start=1):
            if item.predicate not in PREDICATES:
                raise ValueError(f"unknown predicate: {item.predicate}")
            if not item.evidence or not (
                {e.code for e in item.evidence} & CANDIDATE_SPECIFIC_CODES
            ):
                raise EvidenceValidationError("proposal lacks candidate-specific evidence")
            proposal_id = _id()
            validated: list[Any] = []
            for evidence in item.evidence:
                if evidence.code not in EVIDENCE_CODES:
                    raise EvidenceValidationError(f"unknown evidence code: {evidence.code}")
                signal_document = connection.execute(
                    select(extraction_signal.c.document_id).where(
                        extraction_signal.c.id == evidence.signal_id
                    )
                ).scalar_one_or_none()
                if signal_document != document_id:
                    raise EvidenceValidationError(
                        "evidence signal does not belong to proposal document"
                    )
                request = ValidationRequest(
                    document_id,
                    item.candidate_object_id,
                    item.predicate,
                    evidence.code,
                    evidence.signal_id,
                    evidence.matched_object_id,
                )
                if not validator.validate(connection, request):
                    raise EvidenceValidationError(f"evidence failed validation: {evidence.code}")
                validated.append(evidence)
            connection.execute(
                insert(proposal).values(
                    id=proposal_id,
                    document_id=document_id,
                    candidate_object_id=item.candidate_object_id,
                    predicate=item.predicate,
                    score=item.score,
                    rank=rank,
                    state="pending",
                    created_at=_now(),
                )
            )
            for evidence in validated:
                connection.execute(
                    insert(proposal_evidence).values(
                        id=_id(),
                        proposal_id=proposal_id,
                        evidence_code=evidence.code,
                        extraction_signal_id=evidence.signal_id,
                        matched_object_id=evidence.matched_object_id,
                        validation_version=evidence.validation_version,
                    )
                )
            ids.append(proposal_id)
        return ids, False

    def list_proposals(self, document_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                select(
                    proposal.c.id,
                    proposal.c.document_id,
                    proposal.c.candidate_object_id,
                    proposal.c.predicate,
                    proposal.c.score,
                    proposal.c.rank,
                    proposal.c.state,
                    object_table.c.label,
                )
                .join(object_table, object_table.c.id == proposal.c.candidate_object_id)
                .where(proposal.c.document_id == document_id)
                .order_by(proposal.c.rank)
            ).mappings()
            return [dict(row) for row in rows]

    def get_processing_job(self, document_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = (
                connection.execute(
                    select(processing_job).where(processing_job.c.document_id == document_id)
                )
                .mappings()
                .one_or_none()
            )
            return dict(row) if row else None

    def claim_processing_job(self, *, worker_id: str, lease_seconds: int = 300) -> str | None:
        """Claim one pending or expired document job without doing processing work."""
        if lease_seconds <= 0:
            raise ValueError("lease duration must be positive")
        now = _now()
        lease_expires_at = (datetime.now(UTC) + timedelta(seconds=lease_seconds)).isoformat()
        with self.database.transaction() as connection:
            candidate = connection.execute(
                select(processing_job.c.document_id)
                .where(
                    or_(
                        processing_job.c.state == "pending",
                        and_(
                            processing_job.c.state == "processing",
                            processing_job.c.lease_expires_at < now,
                        ),
                    )
                )
                .order_by(processing_job.c.document_id)
                .limit(1)
            ).scalar_one_or_none()
            if candidate is None:
                return None
            if not isinstance(candidate, str):
                raise TypeError("processing job document ID is not text")
            claimed = connection.execute(
                update(processing_job)
                .where(
                    processing_job.c.document_id == candidate,
                    or_(
                        processing_job.c.state == "pending",
                        and_(
                            processing_job.c.state == "processing",
                            processing_job.c.lease_expires_at < now,
                        ),
                    ),
                )
                .values(
                    state="processing",
                    attempt_count=processing_job.c.attempt_count + 1,
                    lease_owner=worker_id,
                    lease_expires_at=lease_expires_at,
                    error_code=None,
                    updated_at=now,
                )
            )
            if claimed.rowcount != 1:
                return None
            connection.execute(
                update(document)
                .where(document.c.object_id == candidate)
                .values(processing_state="processing")
            )
            return candidate

    def fail_processing_job(self, document_id: str, *, worker_id: str, error_code: str) -> None:
        """Durably fail a job only when the caller owns its current lease."""
        if not worker_id.strip() or not error_code.strip():
            raise ValueError("worker ID and error code must be non-empty")
        now = _now()
        with self.database.transaction() as connection:
            failed = connection.execute(
                update(processing_job)
                .where(
                    processing_job.c.document_id == document_id,
                    processing_job.c.state == "processing",
                    processing_job.c.lease_owner == worker_id,
                    processing_job.c.lease_expires_at > now,
                )
                .values(
                    state="failed",
                    lease_owner=None,
                    lease_expires_at=None,
                    error_code=error_code,
                    updated_at=now,
                )
            )
            if failed.rowcount != 1:
                raise ProcessingConflict("worker does not own an active processing lease")
            connection.execute(
                update(document)
                .where(document.c.object_id == document_id)
                .values(processing_state="failed")
            )

    def complete_processing(
        self,
        document_id: str,
        *,
        worker_id: str,
        document_type: str,
        document_date: str | None,
        extractor_version: str,
        signals: Sequence[ExtractionSignalInput],
        proposals: Sequence[ProcessingProposalInput],
        validator: EvidenceValidator,
    ) -> ProcessingCompletionResult:
        """Atomically persist processing output after verifying the active lease."""
        if not worker_id.strip() or not document_type.strip() or not extractor_version.strip():
            raise ValueError("worker, document type, and extractor version must be non-empty")
        now = _now()
        with self.database.transaction() as connection:
            job = (
                connection.execute(
                    select(processing_job).where(processing_job.c.document_id == document_id)
                )
                .mappings()
                .one_or_none()
            )
            if job is None:
                raise KeyError("processing job not found")
            document_row = (
                connection.execute(select(document).where(document.c.object_id == document_id))
                .mappings()
                .one()
            )
            replayed_completion = job["state"] == "complete"
            if replayed_completion:
                if (
                    document_row["processing_state"],
                    document_row["document_type"],
                    document_row["document_date"],
                    document_row["extractor_version"],
                ) != ("complete", document_type, document_date, extractor_version):
                    raise ProcessingConflict("completed document differs from replay")
                self._assert_exact_signal_set(connection, document_id, signals)
                proposal_inputs = self._resolve_processing_proposals(
                    connection, document_id, proposals
                )
                proposal_ids, _proposal_replay = self._persist_proposals(
                    connection, document_id, proposal_inputs, validator
                )
                signal_ids = self._signal_ids_for_inputs(connection, document_id, signals)
                return ProcessingCompletionResult(tuple(signal_ids), tuple(proposal_ids), True)
            elif not (
                job["state"] == "processing"
                and job["lease_owner"] == worker_id
                and job["lease_expires_at"] is not None
                and job["lease_expires_at"] > now
            ):
                raise ProcessingConflict("worker does not own an unexpired processing lease")

            signal_ids = self._store_extraction_signals(connection, document_id, signals)
            self._assert_exact_signal_set(connection, document_id, signals)
            proposal_inputs = self._resolve_processing_proposals(connection, document_id, proposals)
            proposal_ids, _proposal_replay = self._persist_proposals(
                connection, document_id, proposal_inputs, validator
            )

            connection.execute(
                update(document)
                .where(document.c.object_id == document_id)
                .values(
                    document_type=document_type,
                    document_date=document_date,
                    processing_state="complete",
                    extractor_version=extractor_version,
                )
            )
            completed = connection.execute(
                update(processing_job)
                .where(
                    processing_job.c.document_id == document_id,
                    processing_job.c.state == "processing",
                    processing_job.c.lease_owner == worker_id,
                    processing_job.c.lease_expires_at > now,
                )
                .values(
                    state="complete",
                    updated_at=now,
                    lease_owner=None,
                    lease_expires_at=None,
                    error_code=None,
                )
            )
            if completed.rowcount != 1:
                raise ProcessingConflict("processing lease was lost before completion")
            return ProcessingCompletionResult(tuple(signal_ids), tuple(proposal_ids), False)

    @staticmethod
    def _signal_ids_for_inputs(
        connection: Any,
        document_id: str,
        signals: Sequence[ExtractionSignalInput],
    ) -> list[str]:
        ids: list[str] = []
        for signal in signals:
            signal_id = connection.execute(
                select(extraction_signal.c.id).where(
                    extraction_signal.c.document_id == document_id,
                    extraction_signal.c.signal_type == signal.signal_type,
                    extraction_signal.c.normalised_value == signal.normalised_value,
                    extraction_signal.c.source_locator == signal.source_locator,
                )
            ).scalar_one()
            ids.append(signal_id)
        return ids

    @staticmethod
    def _assert_exact_signal_set(
        connection: Any,
        document_id: str,
        signals: Sequence[ExtractionSignalInput],
    ) -> None:
        stored = set(
            connection.execute(
                select(
                    extraction_signal.c.signal_type,
                    extraction_signal.c.normalised_value,
                    extraction_signal.c.display_value,
                    extraction_signal.c.source_locator,
                    extraction_signal.c.extractor,
                    extraction_signal.c.extractor_version,
                ).where(extraction_signal.c.document_id == document_id)
            ).tuples()
        )
        requested = {
            (
                item.signal_type,
                item.normalised_value,
                item.display_value,
                item.source_locator,
                item.extractor,
                item.extractor_version,
            )
            for item in signals
        }
        if stored != requested:
            raise ProcessingConflict("persisted extraction differs from completion request")

    @staticmethod
    def _resolve_processing_proposals(
        connection: Any,
        document_id: str,
        proposals: Sequence[ProcessingProposalInput],
    ) -> tuple[ProposalInput, ...]:
        resolved: list[ProposalInput] = []
        for item in proposals:
            evidence_inputs: list[ProposalEvidenceInput] = []
            for evidence in item.evidence:
                signal_id = connection.execute(
                    select(extraction_signal.c.id).where(
                        extraction_signal.c.document_id == document_id,
                        extraction_signal.c.signal_type == evidence.signal_type,
                        extraction_signal.c.normalised_value == evidence.normalised_value,
                        extraction_signal.c.source_locator == evidence.source_locator,
                    )
                ).scalar_one_or_none()
                if signal_id is None:
                    raise EvidenceValidationError(
                        "proposal evidence does not reference completion extraction"
                    )
                evidence_inputs.append(
                    ProposalEvidenceInput(
                        evidence.code,
                        signal_id,
                        evidence.matched_object_id,
                        evidence.validation_version,
                    )
                )
            resolved.append(
                ProposalInput(
                    item.candidate_object_id,
                    item.predicate,
                    item.score,
                    tuple(evidence_inputs),
                )
            )
        return tuple(resolved)

    def retry_processing_job(
        self,
        document_id: str,
        *,
        actor: str,
        idempotency_key: str,
        max_attempts: int = 3,
    ) -> RetryProcessingResult:
        """Audit and transition one failed processing job back to pending."""
        if not actor.strip() or not idempotency_key.strip():
            raise ValueError("actor and idempotency key must be non-empty")
        if max_attempts <= 0:
            raise ValueError("maximum attempts must be positive")
        semantics = {
            "actor": actor,
            "document_id": document_id,
            "transition": "failed_to_pending",
        }
        payload = json.dumps(semantics, sort_keys=True, separators=(",", ":"))
        with self.database.transaction() as connection:
            existing = (
                connection.execute(
                    select(audit_event).where(
                        audit_event.c.action == "processing.retry",
                        audit_event.c.correlation_key == idempotency_key,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                if existing["payload"] != payload or existing["actor"] != actor:
                    raise IdempotencyConflict(
                        "retry idempotency key belongs to different semantics"
                    )
                return RetryProcessingResult(document_id, existing["id"], True)

            job = (
                connection.execute(
                    select(processing_job).where(processing_job.c.document_id == document_id)
                )
                .mappings()
                .one_or_none()
            )
            if job is None:
                raise KeyError("processing job not found")
            if job["state"] != "failed":
                raise ProcessingConflict("only failed processing jobs may be retried")
            if job["attempt_count"] >= max_attempts:
                raise ProcessingConflict("processing retry attempt limit reached")

            now = _now()
            retried = connection.execute(
                update(processing_job)
                .where(
                    processing_job.c.document_id == document_id,
                    processing_job.c.state == "failed",
                    processing_job.c.attempt_count < max_attempts,
                )
                .values(
                    state="pending",
                    lease_owner=None,
                    lease_expires_at=None,
                    error_code=None,
                    updated_at=now,
                )
            )
            if retried.rowcount != 1:
                raise ProcessingConflict("processing job changed before retry")
            connection.execute(
                update(document)
                .where(document.c.object_id == document_id)
                .values(processing_state="pending")
            )
            audit_id = _id()
            connection.execute(
                insert(audit_event).values(
                    id=audit_id,
                    actor=actor,
                    action="processing.retry",
                    affected_id=document_id,
                    correlation_key=idempotency_key,
                    payload=payload,
                    payload_hash=hashlib.sha256(payload.encode()).hexdigest(),
                    created_at=now,
                )
            )
            return RetryProcessingResult(document_id, audit_id, False)

    def query_related_documents(self, object_id: str) -> list[RelatedDocument]:
        with self.database.connect() as connection:
            rows = list(
                connection.execute(
                    select(
                        document.c.object_id,
                        object_table.c.label,
                        document.c.document_type,
                        document.c.document_date,
                        relation.c.predicate,
                        relation.c.id.label("relation_id"),
                    )
                    .join(relation, relation.c.document_id == document.c.object_id)
                    .join(object_table, object_table.c.id == document.c.object_id)
                    .where(
                        and_(
                            relation.c.target_object_id == object_id,
                            relation.c.status == "confirmed",
                        )
                    )
                    .order_by(document.c.object_id)
                ).mappings()
            )
            output: list[RelatedDocument] = []
            for row in rows:
                explanations = connection.execute(
                    select(proposal_evidence.c.evidence_code, extraction_signal.c.display_value)
                    .select_from(
                        evidence_link.join(
                            proposal_evidence,
                            proposal_evidence.c.id == evidence_link.c.proposal_evidence_id,
                        ).join(
                            extraction_signal,
                            extraction_signal.c.id == proposal_evidence.c.extraction_signal_id,
                        )
                    )
                    .where(evidence_link.c.relation_id == row["relation_id"])
                    .order_by(proposal_evidence.c.evidence_code, extraction_signal.c.id)
                ).all()
                output.append(
                    RelatedDocument(
                        row["object_id"],
                        row["label"],
                        row["document_type"],
                        row["document_date"],
                        row["predicate"],
                        row["relation_id"],
                        tuple((code, value) for code, value in explanations),
                    )
                )
            return output


class ConfirmationService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def confirm(
        self, *, proposal_id: str, reviewer: str, idempotency_key: str, validator: EvidenceValidator
    ) -> ConfirmationResult:
        if not reviewer.strip() or not idempotency_key.strip():
            raise ValueError("reviewer and idempotency key must be non-empty")
        with self.database.transaction() as connection:
            replay = (
                connection.execute(
                    select(review_decision).where(
                        review_decision.c.idempotency_key == idempotency_key
                    )
                )
                .mappings()
                .one_or_none()
            )
            if replay is not None:
                if (
                    replay["proposal_id"] != proposal_id
                    or replay["decision"] != "accepted"
                    or replay["reviewer"] != reviewer
                ):
                    raise IdempotencyConflict(
                        "confirmation idempotency key belongs to different semantics"
                    )
                relation_id = connection.execute(
                    select(relation.c.id).where(relation.c.confirmation_decision_id == replay["id"])
                ).scalar_one()
                return ConfirmationResult(replay["id"], relation_id, True)

            proposal_row = (
                connection.execute(select(proposal).where(proposal.c.id == proposal_id))
                .mappings()
                .one_or_none()
            )
            if proposal_row is None:
                raise KeyError("proposal not found")
            accepted = (
                connection.execute(
                    select(review_decision).where(
                        review_decision.c.proposal_id == proposal_id,
                        review_decision.c.decision == "accepted",
                    )
                )
                .mappings()
                .one_or_none()
            )
            if accepted is not None:
                if accepted["reviewer"] != reviewer:
                    raise IdempotencyConflict(
                        "proposal was already confirmed by a different reviewer"
                    )
                relation_id = connection.execute(
                    select(relation.c.id).where(
                        relation.c.confirmation_decision_id == accepted["id"]
                    )
                ).scalar_one()
                return ConfirmationResult(accepted["id"], relation_id, True)
            evidence_rows = list(
                connection.execute(
                    select(proposal_evidence)
                    .where(proposal_evidence.c.proposal_id == proposal_id)
                    .order_by(proposal_evidence.c.id)
                ).mappings()
            )
            if not evidence_rows:
                raise EvidenceValidationError("proposal has no evidence")
            for evidence in evidence_rows:
                request = ValidationRequest(
                    proposal_row["document_id"],
                    proposal_row["candidate_object_id"],
                    proposal_row["predicate"],
                    evidence["evidence_code"],
                    evidence["extraction_signal_id"],
                    evidence["matched_object_id"],
                )
                if not validator.validate(connection, request):
                    raise EvidenceValidationError(
                        f"evidence became invalid: {evidence['evidence_code']}"
                    )

            decision_id, relation_id = _id(), _id()
            now = _now()
            connection.execute(
                insert(review_decision).values(
                    id=decision_id,
                    proposal_id=proposal_id,
                    reviewer=reviewer,
                    decision="accepted",
                    idempotency_key=idempotency_key,
                    decided_at=now,
                )
            )
            connection.execute(
                insert(relation).values(
                    id=relation_id,
                    document_id=proposal_row["document_id"],
                    predicate=proposal_row["predicate"],
                    target_object_id=proposal_row["candidate_object_id"],
                    status="confirmed",
                    confirmation_decision_id=decision_id,
                    created_at=now,
                )
            )
            for evidence in evidence_rows:
                connection.execute(
                    insert(evidence_link).values(
                        relation_id=relation_id, proposal_evidence_id=evidence["id"]
                    )
                )
            connection.execute(
                update(proposal).where(proposal.c.id == proposal_id).values(state="accepted")
            )
            payload = json.dumps(
                {
                    "decision_id": decision_id,
                    "proposal_id": proposal_id,
                    "relation_id": relation_id,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            connection.execute(
                insert(audit_event).values(
                    id=_id(),
                    actor=reviewer,
                    action="proposal.accepted",
                    affected_id=relation_id,
                    correlation_key=idempotency_key,
                    payload=payload,
                    payload_hash=hashlib.sha256(payload.encode()).hexdigest(),
                    created_at=now,
                )
            )
            return ConfirmationResult(decision_id, relation_id, False)
