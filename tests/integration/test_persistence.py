from __future__ import annotations

import io
from pathlib import Path

import pytest
from sqlalchemy import func, inspect, select, text, update

from reversecrm.db import (
    ConfirmationService,
    Database,
    EvidenceValidationError,
    ExtractionSignalInput,
    PersistenceService,
    ProposalEvidenceInput,
    ProposalInput,
)
from reversecrm.db.contracts import ValidationRequest
from reversecrm.db.schema import (
    audit_event,
    evidence_blob,
    property_table,
    proposal,
    relation,
    review_decision,
    source_reference,
)
from reversecrm.evidence import EvidenceStore, IntegrityService
from reversecrm.match.persistence import SqlEvidenceValidator


class ToggleValidator:
    def __init__(self, valid: bool = True) -> None:
        self.valid = valid
        self.requests: list[ValidationRequest] = []

    def validate(self, connection: object, request: ValidationRequest) -> bool:
        self.requests.append(request)
        return self.valid


@pytest.fixture
def core(tmp_path: Path) -> tuple[Database, PersistenceService, EvidenceStore]:
    database = Database(tmp_path / "db.sqlite3")
    database.migrate()
    return database, PersistenceService(database), EvidenceStore(tmp_path / "evidence")


def _submitted(
    service: PersistenceService, store: EvidenceStore, identity: str = "upload-1"
) -> str:
    placement = store.place(io.BytesIO(b"synthetic council rates"), "application/pdf")
    return service.ingest_placement(
        source_kind="direct",
        source_identity=identity,
        placement=placement,
        original_name="rates.pdf",
    ).document_id


def test_strict_schema_and_connection_policy(
    core: tuple[Database, PersistenceService, EvidenceStore],
) -> None:
    database, _, _ = core
    with database.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert connection.execute(text("PRAGMA synchronous")).scalar_one() == 2
        assert connection.execute(text("PRAGMA trusted_schema")).scalar_one() == 0
        assert connection.execute(text("PRAGMA journal_mode")).scalar_one() == "wal"
        table_sql = connection.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='document'")
        ).scalar_one()
        assert table_sql.strip().endswith("STRICT")
        assert set(inspect(connection).get_table_names()) >= {
            "alembic_version",
            "object",
            "document",
            "proposal",
            "relation",
            "audit_event",
        }


def test_stream_placement_and_source_document_reuse(
    core: tuple[Database, PersistenceService, EvidenceStore],
) -> None:
    database, service, store = core
    placement = store.place(io.BytesIO(b"same immutable bytes"), "application/pdf")
    first = service.ingest_placement(
        source_kind="direct", source_identity="one", placement=placement
    )
    replay = service.ingest_placement(
        source_kind="direct", source_identity="one", placement=placement
    )
    distinct = service.ingest_placement(
        source_kind="direct", source_identity="two", placement=placement
    )

    assert replay.replayed_source and replay.source_reference_id == first.source_reference_id
    assert distinct.reused_document and distinct.document_id == first.document_id
    assert store.resolve(placement.relative_path).read_bytes() == b"same immutable bytes"
    with database.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(evidence_blob)) == 1
        assert connection.scalar(select(func.count()).select_from(source_reference)) == 2


def test_bounded_proposal_confirmation_idempotency_and_relationship_query(
    core: tuple[Database, PersistenceService, EvidenceStore],
) -> None:
    database, service, store = core
    property_id = service.create_property("Rental", "1 test street", "rental")
    document_id = _submitted(service, store)
    signal_id = service.store_extraction_signals(
        document_id,
        [
            ExtractionSignalInput(
                "property_address", "1 test street", "1 Test Street", "page:1", "fixture", "1"
            )
        ],
    )[0]
    validator = ToggleValidator()
    proposal_ids = service.persist_proposals(
        document_id,
        [
            ProposalInput(
                property_id,
                "concerns_property",
                100,
                (ProposalEvidenceInput("address_exact", signal_id, property_id),),
            )
        ],
        validator,
    )
    confirmation = ConfirmationService(database)
    first = confirmation.confirm(
        proposal_id=proposal_ids[0],
        reviewer="local-user",
        idempotency_key="confirm-1",
        validator=validator,
    )
    replay = confirmation.confirm(
        proposal_id=proposal_ids[0],
        reviewer="local-user",
        idempotency_key="confirm-1",
        validator=validator,
    )
    replay_with_new_key = confirmation.confirm(
        proposal_id=proposal_ids[0],
        reviewer="local-user",
        idempotency_key="confirm-retry",
        validator=validator,
    )

    assert replay.replayed and replay.decision_id == first.decision_id
    assert replay_with_new_key.replayed
    assert replay_with_new_key.decision_id == first.decision_id
    related = service.query_related_documents(property_id)
    assert [(item.document_id, item.predicate) for item in related] == [
        (document_id, "concerns_property")
    ]
    assert related[0].evidence == (("address_exact", "1 Test Street"),)
    with database.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(review_decision)) == 1
        assert connection.scalar(select(func.count()).select_from(relation)) == 1
        assert connection.scalar(select(func.count()).select_from(audit_event)) == 1


def test_document_processing_job_has_an_explicit_lease_boundary(
    core: tuple[Database, PersistenceService, EvidenceStore],
) -> None:
    _, service, store = core
    document_id = _submitted(service, store)

    assert service.claim_processing_job(worker_id="worker-1", lease_seconds=60) == document_id
    assert service.claim_processing_job(worker_id="worker-2", lease_seconds=60) is None
    with pytest.raises(ValueError, match="does not own"):
        service.fail_processing_job(document_id, worker_id="worker-2", error_code="ocr_failed")
    service.fail_processing_job(document_id, worker_id="worker-1", error_code="ocr_failed")
    job = service.get_processing_job(document_id)
    assert job is not None
    assert job["state"] == "failed"
    assert job["attempt_count"] == 1
    assert job["lease_owner"] is None
    assert job["error_code"] == "ocr_failed"


def test_validation_failure_rolls_back_confirmation(
    core: tuple[Database, PersistenceService, EvidenceStore],
) -> None:
    database, service, store = core
    asset_id = service.create_asset("Fridge", model="cold-1")
    document_id = _submitted(service, store)
    signal_id = service.store_extraction_signals(
        document_id,
        [ExtractionSignalInput("model", "cold-1", "COLD-1", "page:1", "fixture", "1")],
    )[0]
    validator = ToggleValidator()
    proposal_id = service.persist_proposals(
        document_id,
        [
            ProposalInput(
                asset_id,
                "purchase_evidence_for",
                50,
                (ProposalEvidenceInput("model_exact", signal_id, asset_id),),
            )
        ],
        validator,
    )[0]
    validator.valid = False
    with pytest.raises(EvidenceValidationError):
        ConfirmationService(database).confirm(
            proposal_id=proposal_id,
            reviewer="local-user",
            idempotency_key="bad-confirm",
            validator=validator,
        )
    with database.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(review_decision)) == 0
        assert connection.scalar(select(func.count()).select_from(relation)) == 0
        assert connection.scalar(select(func.count()).select_from(audit_event)) == 0
        assert (
            connection.scalar(select(proposal.c.state).where(proposal.c.id == proposal_id))
            == "pending"
        )


def test_real_validator_rejects_stale_evidence_and_rolls_back(
    core: tuple[Database, PersistenceService, EvidenceStore],
) -> None:
    database, service, store = core
    property_id = service.create_property("Rental", "1 test street", "rental")
    document_id = _submitted(service, store)
    signal_id = service.store_extraction_signals(
        document_id,
        [
            ExtractionSignalInput(
                "property_address", "1 test street", "1 Test Street", "page:1", "fixture", "1"
            )
        ],
    )[0]
    validator = SqlEvidenceValidator()
    proposal_id = service.persist_proposals(
        document_id,
        [
            ProposalInput(
                property_id,
                "concerns_property",
                100,
                (ProposalEvidenceInput("address_exact", signal_id, property_id),),
            )
        ],
        validator,
    )[0]
    with database.transaction() as connection:
        connection.execute(
            update(property_table)
            .where(property_table.c.object_id == property_id)
            .values(normalised_address="changed address")
        )

    with pytest.raises(EvidenceValidationError, match="became invalid"):
        ConfirmationService(database).confirm(
            proposal_id=proposal_id,
            reviewer="local-user",
            idempotency_key="stale-confirm",
            validator=validator,
        )
    with database.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(review_decision)) == 0
        assert connection.scalar(select(func.count()).select_from(relation)) == 0
        assert connection.scalar(select(func.count()).select_from(audit_event)) == 0


def test_closed_registry_bound_and_integrity(
    core: tuple[Database, PersistenceService, EvidenceStore],
) -> None:
    _, service, store = core
    asset_id = service.create_asset("Fridge", model="cold-1")
    document_id = _submitted(service, store)
    signal_id = service.store_extraction_signals(
        document_id,
        [ExtractionSignalInput("model", "cold-1", "COLD-1", "page:1", "fixture", "1")],
    )[0]
    with pytest.raises(ValueError, match="at most three"):
        service.persist_proposals(
            document_id,
            [
                ProposalInput(
                    asset_id,
                    "purchase_evidence_for",
                    rank,
                    (ProposalEvidenceInput("model_exact", signal_id, asset_id),),
                )
                for rank in range(4)
            ],
            ToggleValidator(),
        )
    report = IntegrityService(service.database, store).inspect()
    assert report.database_ok
    assert not report.foreign_key_errors
    assert not report.missing_evidence
    assert not report.orphan_evidence
