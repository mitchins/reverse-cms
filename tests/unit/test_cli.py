from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest
from typer.testing import CliRunner

from reversecrm import cli
from reversecrm.config import Settings
from reversecrm.db import ProcessingConflict, RetryProcessingResult
from reversecrm.db.contracts import IntegrityReport

runner = CliRunner()


class EngineFake:
    def __init__(self) -> None:
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True


class DatabaseFake:
    instances: ClassVar[list[DatabaseFake]] = []

    def __init__(self, path: Path) -> None:
        self.path = path
        self.engine = EngineFake()
        self.migrations = 0
        self.instances.append(self)

    def migrate(self) -> None:
        self.migrations += 1


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'reversecrm.sqlite3'}",
    )


def test_generate_fixtures_cli_writes_and_checks_bundle(tmp_path: Path) -> None:
    output = tmp_path / "fixtures"

    written = runner.invoke(cli.app, ["generate-fixtures", "--output", str(output)])
    checked = runner.invoke(cli.app, ["generate-fixtures", "--output", str(output), "--check"])

    assert written.exit_code == 0
    assert checked.exit_code == 0
    assert "verified" in checked.stdout


def test_integrity_cli_reports_healthy_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    DatabaseFake.instances.clear()
    monkeypatch.setattr(cli, "Settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(cli, "Database", DatabaseFake)
    monkeypatch.setattr(
        cli,
        "IntegrityService",
        lambda database, evidence: SimpleNamespace(
            inspect=lambda: IntegrityReport(True, (), (), ())
        ),
    )

    result = runner.invoke(cli.app, ["integrity"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["database_ok"] is True
    assert DatabaseFake.instances[0].migrations == 1
    assert DatabaseFake.instances[0].engine.disposed is True


def test_retry_cli_wires_audited_transition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    DatabaseFake.instances.clear()
    observed: dict[str, object] = {}
    monkeypatch.setattr(cli, "Settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(cli, "Database", DatabaseFake)

    class PersistenceFake:
        def __init__(self, database: object) -> None:
            pass

        def retry_processing_job(self, document_id: str, **kwargs: object) -> RetryProcessingResult:
            observed.update(document_id=document_id, **kwargs)
            return RetryProcessingResult(document_id, "audit-1", False)

    monkeypatch.setattr(cli, "PersistenceService", PersistenceFake)

    result = runner.invoke(
        cli.app,
        [
            "retry",
            "document-1",
            "--idempotency-key",
            "operator-retry-1",
            "--actor",
            "operator",
        ],
    )

    assert result.exit_code == 0
    assert observed == {
        "document_id": "document-1",
        "actor": "operator",
        "idempotency_key": "operator-retry-1",
        "max_attempts": 3,
    }
    assert json.loads(result.stdout) == {
        "audit_event_id": "audit-1",
        "document_id": "document-1",
        "replayed": False,
    }
    assert DatabaseFake.instances[0].engine.disposed is True


def test_retry_cli_maps_typed_conflict_without_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "Settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(cli, "Database", DatabaseFake)

    class PersistenceFake:
        def __init__(self, database: object) -> None:
            pass

        def retry_processing_job(self, document_id: str, **kwargs: object) -> None:
            raise ProcessingConflict("private state detail")

    monkeypatch.setattr(cli, "PersistenceService", PersistenceFake)

    result = runner.invoke(
        cli.app,
        ["retry", "document-1", "--idempotency-key", "operator-retry-1"],
    )

    assert result.exit_code == 2
    assert "processing_conflict" in result.stderr
    assert "private state detail" not in result.stderr
