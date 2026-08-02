"""Executable acceptance driver over the same core used by web and worker."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from sqlalchemy import insert, select

from reversecrm.core import (
    ConfirmationView,
    CoreApplication,
    PersistenceCounts,
    ProposalView,
    RelatedDocumentView,
    SubmissionView,
)
from reversecrm.db import Database
from reversecrm.db.schema import (
    account,
    asset,
    object_table,
    organisation,
    organisation_alias,
    property_table,
)
from reversecrm.domain.normalise import normalise_text
from reversecrm.evidence import EvidenceStore


@dataclass(frozen=True, slots=True)
class AcceptanceSubmission:
    _application: CoreApplication
    _submission: SubmissionView
    _seed_subject_ids: frozenset[str]

    @property
    def document_id(self) -> str:
        return self._submission.document_id

    @property
    def blob_id(self) -> str:
        return self._submission.blob_id

    @property
    def proposals(self) -> tuple[ProposalView, ...]:
        return self._submission.proposals

    @property
    def persistence_counts(self) -> PersistenceCounts:
        return self._submission.persistence_counts

    @property
    def created_subject_ids(self) -> list[str]:
        with self._application.database.connect() as connection:
            current = set(
                connection.execute(
                    select(object_table.c.id).where(object_table.c.kind != "document")
                ).scalars()
            )
        return sorted(current - self._seed_subject_ids)

    @property
    def extracted_signals(self) -> dict[str, str]:
        rows = self._application.persistence.list_extraction_signals(self.document_id)
        return {row["signal_type"]: row["normalised_value"] for row in rows}

    @property
    def persisted_predicates(self) -> tuple[str, ...]:
        return tuple(item.predicate for item in self.proposals)

    def all_evidence_revalidates(self) -> bool:
        return self._application.all_evidence_revalidates(self.document_id)


class AcceptanceDriver:
    def __init__(self, root: Path) -> None:
        self.database = Database(root / "reversecrm.sqlite3")
        self.application = CoreApplication(self.database, EvidenceStore(root / "evidence"))
        self.seed_subject_ids: frozenset[str] = frozenset()

    def __enter__(self) -> Self:
        self.database.migrate()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.database.engine.dispose()

    def seed(self, seed: dict[str, list[dict[str, Any]]]) -> None:
        self.seed_subject_ids = frozenset(
            item["id"]
            for group in ("organisations", "properties", "accounts", "assets")
            for item in seed[group]
        )
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            for item in seed["organisations"]:
                connection.execute(
                    insert(object_table).values(
                        id=item["id"],
                        kind="organisation",
                        label=item["canonical_name"],
                        created_at=now,
                        updated_at=now,
                    )
                )
                connection.execute(
                    insert(organisation).values(
                        object_id=item["id"],
                        canonical_name=item["canonical_name"],
                        normalised_name=normalise_text(item["canonical_name"]),
                    )
                )
                for alias in item["aliases"]:
                    connection.execute(
                        insert(organisation_alias).values(
                            organisation_id=item["id"], normalised_alias=alias
                        )
                    )
            for item in seed["properties"]:
                self._object(connection, item["id"], "property", item["label"], now)
                connection.execute(
                    insert(property_table).values(
                        object_id=item["id"],
                        normalised_address=item["normalised_address"],
                        occupancy=item["occupancy"],
                    )
                )
            for item in seed["accounts"]:
                self._object(connection, item["id"], "account", item["type"], now)
                connection.execute(
                    insert(account).values(
                        object_id=item["id"],
                        account_type=item["type"],
                        suffix=item["suffix"],
                        issuer_organisation_id=item["issuer_id"],
                        related_subject_id=item["related_subject_id"],
                    )
                )
            for item in seed["assets"]:
                self._object(connection, item["id"], "asset", item["label"], now)
                connection.execute(
                    insert(asset).values(
                        object_id=item["id"],
                        make=item["make"],
                        model=item["model"],
                        serial=item["serial"],
                        order_reference=item["order_reference"],
                    )
                )

    @staticmethod
    def _object(connection: Any, object_id: str, kind: str, label: str, now: str) -> None:
        connection.execute(
            insert(object_table).values(
                id=object_id,
                kind=kind,
                label=label,
                created_at=now,
                updated_at=now,
            )
        )

    def submit_and_process(
        self,
        *,
        content: bytes,
        filename: str,
        source_identity: str,
        network_enabled: bool,
    ) -> AcceptanceSubmission:
        if network_enabled:
            raise ValueError("acceptance core does not enable network access")
        result = self.application.submit_and_process(
            content=content,
            filename=filename,
            source_identity=source_identity,
        )
        return AcceptanceSubmission(self.application, result, self.seed_subject_ids)

    def confirm(self, proposal_id: str, reviewer: str, idempotency_key: str) -> ConfirmationView:
        return self.application.confirm(proposal_id, reviewer, idempotency_key)

    def related_documents(self, object_id: str) -> tuple[RelatedDocumentView, ...]:
        return self.application.related_documents(object_id)
