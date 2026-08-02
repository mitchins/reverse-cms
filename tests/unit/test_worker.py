from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from reversecrm.config import Settings
from reversecrm.db import ProcessingConflict
from reversecrm.ingest.pipeline import ProcessingError
from reversecrm.ingest.worker import (
    SafeWorkerFormatter,
    WorkerApplication,
    _poll_forever,
    run_once,
)


class RuntimeFake:
    def __init__(
        self,
        documents: list[str | None],
        *,
        process_error: Exception | None = None,
        fail_error: Exception | None = None,
    ) -> None:
        self.documents = iter(documents)
        self.process_error = process_error
        self.fail_error = fail_error
        self.calls: list[tuple[str, ...]] = []

    def claim(self, worker_id: str) -> str | None:
        self.calls.append(("claim", worker_id))
        return next(self.documents)

    def process(self, document_id: str, worker_id: str) -> None:
        self.calls.append(("process", document_id, worker_id))
        if self.process_error is not None:
            raise self.process_error

    def fail(self, document_id: str, worker_id: str, error_code: str) -> None:
        self.calls.append(("fail", document_id, worker_id, error_code))
        if self.fail_error is not None:
            raise self.fail_error


@pytest.fixture
def logger_records() -> Iterator[tuple[logging.Logger, list[logging.LogRecord]]]:
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("reversecrm.worker.test")
    logger.handlers = [Capture()]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    yield logger, records
    logger.handlers.clear()


def test_idle_worker_does_not_attempt_processing(
    logger_records: tuple[logging.Logger, list[logging.LogRecord]],
) -> None:
    logger, records = logger_records
    runtime = RuntimeFake([None])

    assert run_once(runtime, worker_id="worker-1", logger=logger) is False

    assert runtime.calls == [("claim", "worker-1")]
    assert [record.msg for record in records] == ["worker_idle"]


def test_claimed_job_completes_once(
    logger_records: tuple[logging.Logger, list[logging.LogRecord]],
) -> None:
    logger, records = logger_records
    runtime = RuntimeFake(["document-1"])

    assert run_once(runtime, worker_id="worker-1", logger=logger) is True

    assert runtime.calls == [
        ("claim", "worker-1"),
        ("process", "document-1", "worker-1"),
    ]
    assert [record.msg for record in records] == ["processing_claimed", "processing_completed"]


def test_processing_failure_preserves_safe_diagnostic_and_marks_failed(
    logger_records: tuple[logging.Logger, list[logging.LogRecord]],
) -> None:
    logger, records = logger_records
    runtime = RuntimeFake(["document-1"], process_error=ProcessingError("text_extraction_timeout"))

    assert run_once(runtime, worker_id="worker-1", logger=logger) is True

    assert runtime.calls[-1] == (
        "fail",
        "document-1",
        "worker-1",
        "text_extraction_timeout",
    )
    failure = records[1]
    assert failure.msg == "processing_failed"
    assert failure.exception_type == "ProcessingError"  # type: ignore[attr-defined]
    assert failure.error_code == "text_extraction_timeout"  # type: ignore[attr-defined]


def test_processing_conflict_preserves_domain_error_code(
    logger_records: tuple[logging.Logger, list[logging.LogRecord]],
) -> None:
    logger, records = logger_records
    runtime = RuntimeFake(
        ["document-1"],
        process_error=ProcessingConflict("private lease detail"),
        fail_error=ProcessingConflict("lease lost"),
    )

    assert run_once(runtime, worker_id="worker-1", logger=logger) is True

    assert runtime.calls[-1] == (
        "fail",
        "document-1",
        "worker-1",
        "processing_conflict",
    )
    diagnostic = json.loads(SafeWorkerFormatter().format(records[1]))
    assert diagnostic["error_code"] == "processing_conflict"
    assert "private lease detail" not in json.dumps(diagnostic)


def test_lost_lease_during_failure_transition_does_not_stop_next_iteration(
    logger_records: tuple[logging.Logger, list[logging.LogRecord]],
) -> None:
    logger, records = logger_records
    runtime = RuntimeFake(
        ["document-1", None],
        process_error=RuntimeError("private extracted text must never be logged"),
        fail_error=ProcessingConflict("lease lost"),
    )

    assert run_once(runtime, worker_id="worker-1", logger=logger) is True
    assert run_once(runtime, worker_id="worker-1", logger=logger) is False

    assert [record.msg for record in records] == [
        "processing_claimed",
        "processing_failed",
        "failure_transition_failed",
        "worker_idle",
    ]
    formatted = SafeWorkerFormatter().format(records[1])
    payload = json.loads(formatted)
    assert payload["exception_type"] == "RuntimeError"
    assert payload["error_code"] == "document_processing_failed"
    assert "private extracted text" not in formatted
    assert "lease lost" not in SafeWorkerFormatter().format(records[2])


def test_worker_initialisation_migrates_once_across_idle_polls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    logger_records: tuple[logging.Logger, list[logging.LogRecord]],
) -> None:
    migrations: list[str] = []

    class EngineFake:
        def dispose(self) -> None:
            pass

    class DatabaseFake:
        engine = EngineFake()

        def __init__(self, path: Path) -> None:
            self.path = path

        def migrate(self) -> None:
            migrations.append("migrate")

    class PersistenceFake:
        def claim_processing_job(self, *, worker_id: str, lease_seconds: int) -> None:
            return None

    class CoreFake:
        persistence = PersistenceFake()

        def __init__(self, database: object, evidence_store: object) -> None:
            pass

    monkeypatch.setattr("reversecrm.ingest.worker.Database", DatabaseFake)
    monkeypatch.setattr("reversecrm.ingest.worker.CoreApplication", CoreFake)
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'worker.sqlite3'}",
    )
    application = WorkerApplication.initialise(settings)
    logger, _records = logger_records

    assert run_once(application, worker_id="worker-1", logger=logger) is False
    assert run_once(application, worker_id="worker-1", logger=logger) is False
    assert migrations == ["migrate"]


def test_claim_failure_is_logged_safely_and_polling_continues(
    logger_records: tuple[logging.Logger, list[logging.LogRecord]],
) -> None:
    logger, records = logger_records
    claim_count = 0

    class ClaimFailureRuntime(RuntimeFake):
        def claim(self, worker_id: str) -> str | None:
            nonlocal claim_count
            claim_count += 1
            if claim_count == 1:
                raise RuntimeError("database URL with secret material")
            return None

    sleeps = 0

    def bounded_sleep(seconds: float) -> None:
        nonlocal sleeps
        assert seconds == 1.0
        sleeps += 1
        if sleeps == 2:
            raise KeyboardInterrupt

    runtime = ClaimFailureRuntime([])
    with pytest.raises(KeyboardInterrupt):
        _poll_forever(
            runtime,
            worker_id="worker-1",
            logger=logger,
            sleep=bounded_sleep,
        )

    assert claim_count == 2
    assert [record.msg for record in records] == ["worker_poll_failed", "worker_idle"]
    diagnostic = SafeWorkerFormatter().format(records[0])
    assert "database URL" not in diagnostic
    assert json.loads(diagnostic)["exception_type"] == "RuntimeError"
