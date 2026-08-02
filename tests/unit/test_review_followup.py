from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from reversecrm.core import (
    CoreApplication,
    PersistenceCounts,
    PreparedProcessing,
    SubmissionReceipt,
)
from reversecrm.evidence import EvidenceStore, IntegrityService
from reversecrm.ingest.pipeline import BoundedTextExtractor, ProcessingError


class SyncPersistenceFake:
    def __init__(self, completion_error: Exception | None = None) -> None:
        self.completion_error = completion_error
        self.claim_kwargs: dict[str, object] = {}
        self.failures: list[tuple[str, str, str]] = []

    def get_processing_job(self, document_id: str) -> dict[str, str]:
        return {"state": "pending"}

    def claim_processing_job(self, **kwargs: object) -> str:
        self.claim_kwargs = kwargs
        return str(kwargs["document_id"])

    def complete_processing(self, document_id: str, **kwargs: object) -> None:
        if self.completion_error is not None:
            raise self.completion_error

    def fail_processing_job(self, document_id: str, *, worker_id: str, error_code: str) -> None:
        self.failures.append((document_id, worker_id, error_code))


def _synchronous_application(persistence: SyncPersistenceFake) -> CoreApplication:
    application = object.__new__(CoreApplication)
    application.persistence = persistence  # type: ignore[assignment]
    application.validator = object()  # type: ignore[assignment]
    application.submit = lambda **_kwargs: SubmissionReceipt("document-target", "blob", False)  # type: ignore[method-assign]
    application.prepare_processing = lambda *_args, **_kwargs: PreparedProcessing(  # type: ignore[method-assign]
        "council_rates", None, "anchor-v1", (), ()
    )
    application.proposals = lambda _document_id: ()  # type: ignore[method-assign]
    application.counts = lambda: PersistenceCounts(0, 0, 0, 0, 0, 0)  # type: ignore[method-assign]
    return application


def test_synchronous_helper_claims_only_the_submitted_document() -> None:
    persistence = SyncPersistenceFake()
    application = _synchronous_application(persistence)

    application.submit_and_process(
        content=b"fixture",
        filename="fixture.pdf",
        source_identity="review-followup",
        text_extractor=BoundedTextExtractor(),
    )

    assert persistence.claim_kwargs["document_id"] == "document-target"


def test_synchronous_helper_marks_owned_lease_failed_before_reraising() -> None:
    persistence = SyncPersistenceFake(ProcessingError("text_extraction_timeout"))
    application = _synchronous_application(persistence)
    extractor = BoundedTextExtractor()

    with pytest.raises(ProcessingError, match="text_extraction_timeout"):
        application.submit_and_process(
            content=b"fixture",
            filename="fixture.pdf",
            source_identity="review-followup",
            text_extractor=extractor,
        )

    assert len(persistence.failures) == 1
    document_id, worker_id, error_code = persistence.failures[0]
    assert document_id == "document-target"
    assert worker_id.startswith("synchronous:")
    assert error_code == "text_extraction_timeout"


class IntegrityRows:
    def __init__(self, rows: list[str]) -> None:
        self.rows = rows

    def scalars(self) -> IntegrityRows:
        return self

    def all(self) -> list[str]:
        return self.rows


class IntegrityConnectionFake:
    def execute(self, statement: object) -> Any:
        sql = str(statement)
        if "integrity_check" in sql:
            return IntegrityRows(["problem one", "problem two"])
        return ()


class IntegrityDatabaseFake:
    @contextmanager
    def connect(self) -> Any:
        yield IntegrityConnectionFake()


def test_integrity_service_reports_multi_row_corruption_without_crashing(tmp_path: Path) -> None:
    report = IntegrityService(
        IntegrityDatabaseFake(),  # type: ignore[arg-type]
        EvidenceStore(tmp_path / "evidence"),
    ).inspect()

    assert report.database_ok is False
