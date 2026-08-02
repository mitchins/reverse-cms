"""Long-lived queue worker with lease-safe diagnostics."""

from __future__ import annotations

import json
import logging
import os
import socket
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from reversecrm.config import Settings
from reversecrm.core import CoreApplication
from reversecrm.db import Database, PersistenceConflict
from reversecrm.evidence import EvidenceStore
from reversecrm.ingest.pipeline import BoundedTextExtractor, ProcessingError

_LOG_FIELDS = ("document_id", "worker_id", "error_code", "exception_type")


class WorkerRuntime(Protocol):
    """Small injectable seam used by the polling loop."""

    def claim(self, worker_id: str) -> str | None: ...

    def process(self, document_id: str, worker_id: str) -> None: ...

    def fail(self, document_id: str, worker_id: str, error_code: str) -> None: ...


class SafeWorkerFormatter(logging.Formatter):
    """Emit only an allowlisted structured diagnostic payload."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, str] = {
            "event": record.getMessage(),
            "level": record.levelname.lower(),
        }
        for field in _LOG_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = str(value)
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))


@dataclass(slots=True)
class WorkerApplication:
    """Long-lived database, core, and extractor resources for one worker process."""

    database: Database
    core: CoreApplication
    extractor: BoundedTextExtractor
    lease_seconds: int

    @classmethod
    def initialise(cls, settings: Settings) -> WorkerApplication:
        database = Database(_database_path(settings))
        database.migrate()
        core = CoreApplication(database, EvidenceStore(settings.data_dir / "evidence"))
        return cls(
            database,
            core,
            BoundedTextExtractor(
                timeout_seconds=settings.ocr_timeout_seconds,
                max_input_bytes=settings.max_upload_bytes,
                max_pdf_pages=settings.max_document_pages,
            ),
            max(settings.ocr_timeout_seconds + 30, 60),
        )

    def close(self) -> None:
        self.database.engine.dispose()

    def claim(self, worker_id: str) -> str | None:
        return self.core.persistence.claim_processing_job(
            worker_id=worker_id, lease_seconds=self.lease_seconds
        )

    def process(self, document_id: str, worker_id: str) -> None:
        prepared = self.core.prepare_processing(document_id, text_extractor=self.extractor)
        self.core.persistence.complete_processing(
            document_id,
            worker_id=worker_id,
            document_type=prepared.document_type,
            document_date=prepared.document_date,
            extractor_version=prepared.extractor_version,
            signals=prepared.signals,
            proposals=prepared.proposals,
            validator=self.core.validator,
        )

    def fail(self, document_id: str, worker_id: str, error_code: str) -> None:
        self.core.persistence.fail_processing_job(
            document_id, worker_id=worker_id, error_code=error_code
        )


def run_once(
    application: WorkerRuntime,
    *,
    worker_id: str,
    logger: logging.Logger,
) -> bool:
    """Claim and process at most one job, returning false only when idle."""

    document_id = application.claim(worker_id)
    if document_id is None:
        logger.debug("worker_idle", extra={"worker_id": worker_id})
        return False

    logger.info(
        "processing_claimed",
        extra={"document_id": document_id, "worker_id": worker_id},
    )
    try:
        application.process(document_id, worker_id)
    except Exception as error:
        error_code = _safe_error_code(error)
        diagnostic = {
            "document_id": document_id,
            "worker_id": worker_id,
            "error_code": error_code,
            "exception_type": type(error).__name__,
        }
        logger.error("processing_failed", extra=diagnostic)
        try:
            application.fail(document_id, worker_id, error_code)
        except Exception as transition_error:
            logger.warning(
                "failure_transition_failed",
                extra={
                    "document_id": document_id,
                    "worker_id": worker_id,
                    "error_code": error_code,
                    "exception_type": type(transition_error).__name__,
                },
            )
        return True

    logger.info(
        "processing_completed",
        extra={"document_id": document_id, "worker_id": worker_id},
    )
    return True


def run_worker() -> None:
    settings = Settings()
    logger = _worker_logger(settings.log_level)
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    application = WorkerApplication.initialise(settings)
    try:
        _poll_forever(application, worker_id=worker_id, logger=logger, sleep=time.sleep)
    finally:
        application.close()


def _poll_forever(
    application: WorkerRuntime,
    *,
    worker_id: str,
    logger: logging.Logger,
    sleep: Callable[[float], None],
) -> None:
    while True:
        try:
            worked = run_once(application, worker_id=worker_id, logger=logger)
        except Exception as error:
            logger.error(
                "worker_poll_failed",
                extra={
                    "worker_id": worker_id,
                    "error_code": "worker_poll_failed",
                    "exception_type": type(error).__name__,
                },
            )
            worked = False
        if not worked:
            sleep(1.0)


def _safe_error_code(error: Exception) -> str:
    if isinstance(error, ProcessingError):
        return error.code
    if isinstance(error, PersistenceConflict):
        return error.code
    if isinstance(error, ValueError):
        return "deterministic_extraction_failed"
    return "document_processing_failed"


def _worker_logger(level: str) -> logging.Logger:
    logger = logging.getLogger("reversecrm.worker")
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(SafeWorkerFormatter())
        logger.addHandler(handler)
        logger.propagate = False
    return logger


def _database_path(settings: Settings) -> Path:
    prefix = "sqlite:///"
    if settings.database_url.startswith(prefix):
        return Path(settings.database_url.removeprefix(prefix))
    raise ValueError("the first slice requires a local SQLite database URL")
