"""Process entry points for the modular monolith."""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Annotated, NoReturn, cast

import typer

from reversecrm.config import Settings
from reversecrm.db import Database, PersistenceConflict, PersistenceService
from reversecrm.evidence import EvidenceStore, IntegrityService
from reversecrm.fixtures import generate_fixture_bundle

app = typer.Typer(no_args_is_help=True, pretty_exceptions_show_locals=False)


def _missing_component(component: str) -> NoReturn:
    typer.echo(f"{component} is not available in this build", err=True)
    raise typer.Exit(code=2)


def _import_component(module_name: str, component: str) -> ModuleType:
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as error:
        if error.name is None or not (
            error.name == module_name or module_name.startswith(f"{error.name}.")
        ):
            raise
        _missing_component(component)


@app.command()
def web() -> None:
    """Run the local web process."""
    settings = Settings()
    settings.require_web_secrets()
    import uvicorn

    _import_component("reversecrm.web.app", "web application")
    uvicorn.run(
        "reversecrm.web.app:create_app",
        factory=True,
        host=settings.bind_host,
        port=settings.bind_port,
        log_level=settings.log_level.lower(),
    )


@app.command()
def worker() -> None:
    """Run the document-processing worker."""
    module = _import_component("reversecrm.ingest.worker", "worker")
    run_worker = cast(Callable[[], None] | None, getattr(module, "run_worker", None))
    if run_worker is None:
        _missing_component("worker entry point")
    run_worker()


@app.command()
def integrity() -> None:
    """Check SQLite integrity and evidence-file registration."""

    settings = Settings()
    database = Database(_database_path(settings))
    try:
        database.migrate()
        report = IntegrityService(database, EvidenceStore(settings.data_dir / "evidence")).inspect()
    finally:
        database.engine.dispose()

    healthy = (
        report.database_ok
        and not report.foreign_key_errors
        and not report.missing_evidence
        and not report.orphan_evidence
    )
    typer.echo(
        json.dumps(
            {
                "database_ok": report.database_ok,
                "foreign_key_errors": report.foreign_key_errors,
                "missing_evidence": report.missing_evidence,
                "orphan_evidence": report.orphan_evidence,
            },
            sort_keys=True,
        )
    )
    if not healthy:
        raise typer.Exit(code=1)


@app.command()
def retry(
    document_id: Annotated[str, typer.Argument(help="Failed document job ID.")],
    idempotency_key: Annotated[
        str,
        typer.Option("--idempotency-key", help="Stable key for safe command replay."),
    ],
    actor: Annotated[
        str | None,
        typer.Option(help="Audit actor; defaults to the configured local reviewer."),
    ] = None,
    max_attempts: Annotated[
        int,
        typer.Option(min=1, max=100, help="Maximum processing attempts."),
    ] = 3,
) -> None:
    """Explicitly return one failed, attempt-bounded job to the queue."""

    settings = Settings()
    database = Database(_database_path(settings))
    try:
        database.migrate()
        result = PersistenceService(database).retry_processing_job(
            document_id,
            actor=actor or settings.reviewer_id,
            idempotency_key=idempotency_key,
            max_attempts=max_attempts,
        )
    except PersistenceConflict as error:
        typer.echo(f"retry rejected: {error.code}", err=True)
        raise typer.Exit(code=2) from error
    finally:
        database.engine.dispose()
    typer.echo(
        json.dumps(
            {
                "audit_event_id": result.audit_event_id,
                "document_id": result.document_id,
                "replayed": result.replayed,
            },
            sort_keys=True,
        )
    )


@app.command("generate-fixtures")
def generate_fixtures(
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Destination for generated synthetic documents."),
    ] = Path("generated-fixtures"),
    check: Annotated[
        bool,
        typer.Option(help="Compare generated files with an existing destination."),
    ] = False,
) -> None:
    """Generate the three synthetic frozen-mission anchor documents."""
    changed = generate_fixture_bundle(output, check=check)
    if check and changed:
        typer.echo("generated fixture bundle is stale", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"fixture bundle {'verified' if check else 'written'} at {output}")


def _database_path(settings: Settings) -> Path:
    prefix = "sqlite:///"
    if settings.database_url.startswith(prefix):
        return Path(settings.database_url.removeprefix(prefix))
    raise ValueError("the first slice requires a local SQLite database URL")
