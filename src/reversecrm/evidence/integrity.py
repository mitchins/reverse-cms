"""Minimal database and immutable-file integrity inspection."""

from __future__ import annotations

from sqlalchemy import select, text

from reversecrm.db.connection import Database
from reversecrm.db.contracts import IntegrityReport
from reversecrm.db.schema import evidence_blob

from .store import EvidenceStore


class IntegrityService:
    def __init__(self, database: Database, evidence_store: EvidenceStore) -> None:
        self.database = database
        self.evidence_store = evidence_store

    def inspect(self) -> IntegrityReport:
        with self.database.connect() as connection:
            database_ok = connection.execute(text("PRAGMA integrity_check")).scalar_one() == "ok"
            foreign_keys = tuple(
                str(tuple(row)) for row in connection.execute(text("PRAGMA foreign_key_check"))
            )
            registered = {
                row.relative_path
                for row in connection.execute(select(evidence_blob.c.relative_path))
            }

        missing = tuple(
            sorted(path for path in registered if not self.evidence_store.resolve(path).is_file())
        )
        present = {
            path.relative_to(self.evidence_store.root).as_posix()
            for path in self.evidence_store.root.glob("*/*/*")
            if path.is_file() and path.parent.parent.name != ".incoming"
        }
        orphan = tuple(sorted(present - registered))
        return IntegrityReport(database_ok, foreign_keys, missing, orphan)
