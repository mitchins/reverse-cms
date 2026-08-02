from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, update

from reversecrm.db import (
    ConfirmationService,
    Database,
    ExtractionSignalInput,
    IdempotencyConflict,
    PersistenceService,
    ProcessingCompletionResult,
    ProcessingConflict,
    ProcessingProposalEvidenceInput,
    ProcessingProposalInput,
    ProposalConflict,
    ProposalEvidenceInput,
    ProposalInput,
    RetryProcessingResult,
)
from reversecrm.db.contracts import EvidencePlacement, ValidationRequest
from reversecrm.db.schema import (
    audit_event,
    document,
    extraction_signal,
    processing_job,
    proposal,
    relation,
    review_decision,
    source_reference,
)
from reversecrm.match.persistence import SqlEvidenceValidator


@pytest.fixture
def persistence(tmp_path: Path) -> tuple[Database, PersistenceService]:
    database = Database(tmp_path / "concurrency.sqlite3")
    database.migrate()
    return database, PersistenceService(database)


def _placement(character: str = "b") -> EvidencePlacement:
    digest = character * 64
    return EvidencePlacement(digest, 4, "application/pdf", f"{digest[:2]}/{digest[2:4]}/{digest}")


def _ingest(service: PersistenceService, identity: str = "source-1") -> str:
    return service.ingest_placement(
        source_kind="test", source_identity=identity, placement=_placement()
    ).document_id


def _proposal_fixture(
    service: PersistenceService,
) -> tuple[str, str, str, ProposalInput, ProcessingProposalInput]:
    property_id = service.create_property("Home", "7 SAMPLE ROAD", "occupied")
    document_id = _ingest(service)
    signal = ExtractionSignalInput(
        "property_address", "7 SAMPLE ROAD", "7 Sample Road", "line:3", "anchor", "anchor-v1"
    )
    signal_id = service.store_extraction_signals(document_id, (signal,))[0]
    persisted = ProposalInput(
        property_id,
        "concerns_property",
        100,
        (ProposalEvidenceInput("address_exact", signal_id, property_id, "exact-v1"),),
    )
    completion = ProcessingProposalInput(
        property_id,
        "concerns_property",
        100,
        (
            ProcessingProposalEvidenceInput(
                "address_exact",
                "property_address",
                "7 SAMPLE ROAD",
                "line:3",
                property_id,
                "exact-v1",
            ),
        ),
    )
    return document_id, property_id, signal_id, persisted, completion


def test_select_only_connection_does_not_reserve_writer(
    persistence: tuple[Database, PersistenceService],
) -> None:
    database, _ = persistence
    entered = threading.Event()

    def writer() -> None:
        with database.transaction():
            entered.set()

    with database.connect() as read_connection:
        assert read_connection.execute(select(func.count()).select_from(document)).scalar_one() == 0
        driver = read_connection.connection.driver_connection
        assert not driver.in_transaction
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(writer)
            assert entered.wait(1)
            future.result(timeout=1)


def test_begin_immediate_serializes_writers_at_transaction_entry(
    persistence: tuple[Database, PersistenceService],
) -> None:
    database, _ = persistence
    first_entered = threading.Event()
    release_first = threading.Event()
    second_attempting = threading.Event()
    second_entered = threading.Event()

    def first_writer() -> None:
        with database.transaction():
            first_entered.set()
            assert release_first.wait(2)

    def second_writer() -> None:
        assert first_entered.wait(2)
        second_attempting.set()
        with database.transaction():
            second_entered.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(first_writer)
        second = pool.submit(second_writer)
        assert second_attempting.wait(1)
        assert not second_entered.wait(0.1)
        release_first.set()
        first.result(timeout=2)
        second.result(timeout=2)
    assert second_entered.is_set()


def test_concurrent_same_source_ingest_replays_without_integrity_error(
    persistence: tuple[Database, PersistenceService],
) -> None:
    database, service = persistence
    barrier = threading.Barrier(2)

    def ingest() -> object:
        barrier.wait()
        return service.ingest_placement(
            source_kind="test", source_identity="same", placement=_placement()
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result() for future in (pool.submit(ingest), pool.submit(ingest))]
    assert results[0].document_id == results[1].document_id
    assert results[0].source_reference_id == results[1].source_reference_id
    assert {item.replayed_source for item in results} == {False, True}
    with database.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(source_reference)) == 1


def test_source_replay_with_different_bytes_is_typed_conflict(
    persistence: tuple[Database, PersistenceService],
) -> None:
    _, service = persistence
    service.ingest_placement(source_kind="test", source_identity="same", placement=_placement())
    with pytest.raises(IdempotencyConflict):
        service.ingest_placement(
            source_kind="test", source_identity="same", placement=_placement("c")
        )


def test_concurrent_proposal_persistence_replays_or_typed_conflicts(
    persistence: tuple[Database, PersistenceService],
) -> None:
    database, service = persistence
    document_id, property_id, _signal_id, proposal_input, _ = _proposal_fixture(service)
    validator = SqlEvidenceValidator()
    barrier = threading.Barrier(2)

    def persist(item: ProposalInput) -> list[str]:
        barrier.wait()
        return service.persist_proposals(document_id, (item,), validator)

    with ThreadPoolExecutor(max_workers=2) as pool:
        same = [
            future.result()
            for future in (
                pool.submit(persist, proposal_input),
                pool.submit(persist, proposal_input),
            )
        ]
    assert same[0] == same[1]

    other_document = service.ingest_placement(
        source_kind="test", source_identity="other", placement=_placement("c")
    ).document_id
    other_signal = service.store_extraction_signals(
        other_document,
        (
            ExtractionSignalInput(
                "property_address",
                "7 SAMPLE ROAD",
                "7 Sample Road",
                "line:3",
                "anchor",
                "anchor-v1",
            ),
        ),
    )[0]
    first = ProposalInput(
        property_id,
        "concerns_property",
        100,
        (ProposalEvidenceInput("address_exact", other_signal, property_id, "exact-v1"),),
    )
    second = ProposalInput(
        property_id,
        "concerns_property",
        90,
        (ProposalEvidenceInput("address_exact", other_signal, property_id, "exact-v1"),),
    )
    conflict_barrier = threading.Barrier(2)

    def conflicting(item: ProposalInput) -> list[str] | ProposalConflict:
        conflict_barrier.wait()
        try:
            return service.persist_proposals(other_document, (item,), validator)
        except ProposalConflict as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [
            future.result()
            for future in (pool.submit(conflicting, first), pool.submit(conflicting, second))
        ]
    assert sum(isinstance(item, ProposalConflict) for item in outcomes) == 1
    with database.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(proposal)) == 2


def test_concurrent_confirmation_has_one_decision_relation_and_audit(
    persistence: tuple[Database, PersistenceService],
) -> None:
    database, service = persistence
    document_id, _, _, proposal_input, _ = _proposal_fixture(service)
    validator = SqlEvidenceValidator()
    proposal_id = service.persist_proposals(document_id, (proposal_input,), validator)[0]
    confirmations = ConfirmationService(database)
    barrier = threading.Barrier(2)

    def confirm() -> object:
        barrier.wait()
        return confirmations.confirm(
            proposal_id=proposal_id,
            reviewer="reviewer",
            idempotency_key="confirm-concurrent",
            validator=validator,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result() for future in (pool.submit(confirm), pool.submit(confirm))]
    assert results[0].decision_id == results[1].decision_id
    assert results[0].relation_id == results[1].relation_id
    with database.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(review_decision)) == 1
        assert connection.scalar(select(func.count()).select_from(relation)) == 1
        assert connection.scalar(select(func.count()).select_from(audit_event)) == 1

    with pytest.raises(IdempotencyConflict):
        confirmations.confirm(
            proposal_id=proposal_id,
            reviewer="other-reviewer",
            idempotency_key="confirm-concurrent",
            validator=validator,
        )


class ExplodingValidator:
    def validate(self, connection: object, request: ValidationRequest) -> bool:
        raise RuntimeError("fault before atomic completion")


def test_atomic_completion_rolls_back_and_verifies_lease(
    persistence: tuple[Database, PersistenceService],
) -> None:
    database, service = persistence
    property_id = service.create_property("Home", "7 SAMPLE ROAD", "occupied")
    document_id = _ingest(service)
    assert service.claim_processing_job(worker_id="worker", lease_seconds=60) == document_id
    signal = ExtractionSignalInput(
        "property_address", "7 SAMPLE ROAD", "7 Sample Road", "line:3", "anchor", "anchor-v1"
    )
    completion_proposal = ProcessingProposalInput(
        property_id,
        "concerns_property",
        100,
        (
            ProcessingProposalEvidenceInput(
                "address_exact",
                "property_address",
                "7 SAMPLE ROAD",
                "line:3",
                property_id,
                "exact-v1",
            ),
        ),
    )
    with pytest.raises(RuntimeError, match="fault before"):
        service.complete_processing(
            document_id,
            worker_id="worker",
            document_type="mortgage_statement",
            document_date=None,
            extractor_version="anchor-v1",
            signals=(signal,),
            proposals=(completion_proposal,),
            validator=ExplodingValidator(),
        )
    with database.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(extraction_signal)) == 0
        assert connection.scalar(select(func.count()).select_from(proposal)) == 0
        assert connection.scalar(select(document.c.processing_state)) == "processing"
        assert connection.scalar(select(processing_job.c.state)) == "processing"

    barrier = threading.Barrier(2)

    def complete() -> ProcessingCompletionResult:
        barrier.wait()
        return service.complete_processing(
            document_id,
            worker_id="worker",
            document_type="mortgage_statement",
            document_date=None,
            extractor_version="anchor-v1",
            signals=(signal,),
            proposals=(completion_proposal,),
            validator=SqlEvidenceValidator(),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result() for future in (pool.submit(complete), pool.submit(complete))]
    assert {item.replayed for item in results} == {False, True}
    assert results[0].proposal_ids == results[1].proposal_ids
    changed_signals = (
        signal,
        ExtractionSignalInput("amount_minor", "10000", "$100.00", "line:4", "anchor", "anchor-v1"),
    )
    with pytest.raises(ProcessingConflict, match="extraction differs"):
        service.complete_processing(
            document_id,
            worker_id="worker",
            document_type="mortgage_statement",
            document_date=None,
            extractor_version="anchor-v1",
            signals=changed_signals,
            proposals=(completion_proposal,),
            validator=SqlEvidenceValidator(),
        )
    with database.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(extraction_signal)) == 1


def test_expired_lease_cannot_complete_or_persist_output(
    persistence: tuple[Database, PersistenceService],
) -> None:
    database, service = persistence
    property_id = service.create_property("Home", "7 SAMPLE ROAD", "occupied")
    document_id = _ingest(service)
    assert service.claim_processing_job(worker_id="worker", lease_seconds=60) == document_id
    with database.transaction() as connection:
        connection.execute(
            update(processing_job)
            .where(processing_job.c.document_id == document_id)
            .values(lease_expires_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat())
        )
    signal = ExtractionSignalInput(
        "property_address", "7 SAMPLE ROAD", "7 Sample Road", "line:3", "anchor", "anchor-v1"
    )
    proposal_input = ProcessingProposalInput(
        property_id,
        "concerns_property",
        100,
        (
            ProcessingProposalEvidenceInput(
                "address_exact",
                "property_address",
                "7 SAMPLE ROAD",
                "line:3",
                property_id,
            ),
        ),
    )
    with pytest.raises(ProcessingConflict):
        service.complete_processing(
            document_id,
            worker_id="worker",
            document_type="mortgage_statement",
            document_date=None,
            extractor_version="anchor-v1",
            signals=(signal,),
            proposals=(proposal_input,),
            validator=SqlEvidenceValidator(),
        )
    with database.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(extraction_signal)) == 0
        assert connection.scalar(select(func.count()).select_from(proposal)) == 0


def test_concurrent_retry_is_semantically_bound_and_audited_once(
    persistence: tuple[Database, PersistenceService],
) -> None:
    database, service = persistence
    document_id = _ingest(service)
    assert service.claim_processing_job(worker_id="worker", lease_seconds=60) == document_id
    service.fail_processing_job(document_id, worker_id="worker", error_code="ocr_failed")
    barrier = threading.Barrier(2)

    def retry(actor: str) -> tuple[str, object]:
        barrier.wait()
        try:
            return (
                actor,
                service.retry_processing_job(
                    document_id,
                    actor=actor,
                    idempotency_key="retry-concurrent",
                    max_attempts=3,
                ),
            )
        except IdempotencyConflict as error:
            return actor, error

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [
            future.result()
            for future in (pool.submit(retry, "operator"), pool.submit(retry, "other"))
        ]
    assert sum(isinstance(item, IdempotencyConflict) for _, item in outcomes) == 1
    success_actor, _success = next(
        (actor, item) for actor, item in outcomes if not isinstance(item, IdempotencyConflict)
    )
    exact_replay = service.retry_processing_job(
        document_id,
        actor=success_actor,
        idempotency_key="retry-concurrent",
        max_attempts=3,
    )
    assert exact_replay.replayed
    with database.connect() as connection:
        assert (
            connection.scalar(
                select(func.count())
                .select_from(audit_event)
                .where(audit_event.c.action == "processing.retry")
            )
            == 1
        )
        assert connection.scalar(select(processing_job.c.state)) == "pending"


def test_concurrent_exact_retry_replays_one_audit_event(
    persistence: tuple[Database, PersistenceService],
) -> None:
    database, service = persistence
    document_id = _ingest(service)
    assert service.claim_processing_job(worker_id="worker", lease_seconds=60) == document_id
    service.fail_processing_job(document_id, worker_id="worker", error_code="ocr_failed")
    barrier = threading.Barrier(2)

    def retry() -> RetryProcessingResult:
        barrier.wait()
        return service.retry_processing_job(
            document_id,
            actor="operator",
            idempotency_key="retry-exact",
            max_attempts=3,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result() for future in (pool.submit(retry), pool.submit(retry))]
    assert results[0].audit_event_id == results[1].audit_event_id
    assert {item.replayed for item in results} == {False, True}
    with database.connect() as connection:
        assert (
            connection.scalar(
                select(func.count())
                .select_from(audit_event)
                .where(audit_event.c.action == "processing.retry")
            )
            == 1
        )


def test_retry_attempt_bound_is_typed_conflict(
    persistence: tuple[Database, PersistenceService],
) -> None:
    _, service = persistence
    document_id = _ingest(service)
    assert service.claim_processing_job(worker_id="worker", lease_seconds=60) == document_id
    service.fail_processing_job(document_id, worker_id="worker", error_code="ocr_failed")
    with pytest.raises(ProcessingConflict, match="attempt limit"):
        service.retry_processing_job(
            document_id,
            actor="operator",
            idempotency_key="retry-bound",
            max_attempts=1,
        )
