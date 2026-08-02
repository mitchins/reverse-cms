"""SQL-backed exact candidate projection and evidence revalidation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from sqlalchemy import Connection, Select, select

from reversecrm.db.connection import Database
from reversecrm.db.contracts import ValidationRequest
from reversecrm.db.schema import (
    account,
    asset,
    extraction_signal,
    object_table,
    organisation,
    organisation_alias,
    property_table,
)
from reversecrm.domain.models import CandidateSnapshot, EvidenceCode, SignalType


class SqlCandidateReader:
    """Retrieve at most ``limit`` candidates through exact indexed fields."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def find_property_candidates(
        self, signals: Mapping[SignalType, tuple[str, ...]], *, limit: int
    ) -> Sequence[CandidateSnapshot]:
        addresses = signals.get(SignalType.PROPERTY_ADDRESS, ())
        suffixes = signals.get(SignalType.ACCOUNT_SUFFIX, ())
        candidate_ids: list[str] = []
        with self.database.connect() as connection:
            if addresses:
                rows = connection.execute(
                    select(property_table.c.object_id)
                    .where(property_table.c.normalised_address.in_(addresses))
                    .order_by(property_table.c.object_id)
                    .limit(limit)
                ).scalars()
                candidate_ids.extend(rows)
            if suffixes and len(candidate_ids) < limit:
                rows = connection.execute(
                    select(account.c.related_subject_id)
                    .join(
                        property_table,
                        property_table.c.object_id == account.c.related_subject_id,
                    )
                    .where(account.c.suffix.in_(suffixes))
                    .order_by(account.c.related_subject_id)
                    .limit(limit)
                ).scalars()
                candidate_ids.extend(rows)
            candidate_ids = list(dict.fromkeys(candidate_ids))[:limit]
            return [self._property_snapshot(connection, item, signals) for item in candidate_ids]

    def _property_snapshot(
        self,
        connection: Connection,
        object_id: str,
        signals: Mapping[SignalType, tuple[str, ...]],
    ) -> CandidateSnapshot:
        row = connection.execute(
            select(object_table.c.label, property_table.c.normalised_address)
            .join(property_table, property_table.c.object_id == object_table.c.id)
            .where(object_table.c.id == object_id)
        ).one()
        account_rows = list(
            connection.execute(
                select(
                    account.c.object_id,
                    account.c.suffix,
                    organisation.c.normalised_name,
                    organisation_alias.c.normalised_alias,
                )
                .join(organisation, organisation.c.object_id == account.c.issuer_organisation_id)
                .outerjoin(
                    organisation_alias,
                    organisation_alias.c.organisation_id == organisation.c.object_id,
                )
                .where(account.c.related_subject_id == object_id)
                .order_by(account.c.object_id, organisation_alias.c.normalised_alias)
            )
        )
        issuer_names = tuple(dict.fromkeys(item.normalised_name for item in account_rows))
        suffixes = tuple(dict.fromkeys(item.suffix for item in account_rows))
        matched: dict[EvidenceCode, str] = {}
        issuer_values = set(signals.get(SignalType.ISSUER_NAME, ()))
        suffix_values = set(signals.get(SignalType.ACCOUNT_SUFFIX, ()))
        issuer_account = next(
            (item.object_id for item in account_rows if item.normalised_name in issuer_values),
            None,
        )
        suffix_account = next(
            (item.object_id for item in account_rows if item.suffix in suffix_values),
            None,
        )
        if issuer_account is not None:
            matched[EvidenceCode.ISSUER_EXACT] = issuer_account
        if suffix_account is not None:
            matched[EvidenceCode.ACCOUNT_SUFFIX_EXACT] = suffix_account
        return CandidateSnapshot(
            object_id,
            "property",
            row.label,
            address=row.normalised_address,
            account_suffixes=suffixes,
            issuer_names=issuer_names,
            matched_object_ids=matched,
        )

    def find_asset_candidates(
        self, signals: Mapping[SignalType, tuple[str, ...]], *, limit: int
    ) -> Sequence[CandidateSnapshot]:
        lookups = (
            (asset.c.serial, signals.get(SignalType.SERIAL, ())),
            (asset.c.order_reference, signals.get(SignalType.ORDER_REFERENCE, ())),
            (asset.c.model, signals.get(SignalType.MODEL, ())),
        )
        candidate_ids: list[str] = []
        with self.database.connect() as connection:
            for column, values in lookups:
                if not values or len(candidate_ids) >= limit:
                    continue
                rows = connection.execute(
                    select(asset.c.object_id)
                    .where(column.in_(values))
                    .order_by(asset.c.object_id)
                    .limit(limit)
                ).scalars()
                candidate_ids.extend(rows)
                candidate_ids = list(dict.fromkeys(candidate_ids))
            merchant = self._matched_merchant(connection, signals.get(SignalType.MERCHANT_NAME, ()))
            return [
                self._asset_snapshot(connection, item, merchant) for item in candidate_ids[:limit]
            ]

    @staticmethod
    def _matched_merchant(
        connection: Connection, names: tuple[str, ...]
    ) -> tuple[str, tuple[str, ...]] | None:
        if not names:
            return None
        row = connection.execute(
            select(organisation_alias.c.organisation_id)
            .join(
                organisation,
                organisation.c.object_id == organisation_alias.c.organisation_id,
            )
            .where(organisation_alias.c.normalised_alias.in_(names))
            .order_by(organisation_alias.c.organisation_id)
            .limit(1)
        ).scalar_one_or_none()
        if row is None:
            return None
        aliases = connection.execute(
            select(organisation_alias.c.normalised_alias)
            .where(organisation_alias.c.organisation_id == row)
            .order_by(organisation_alias.c.normalised_alias)
        ).scalars()
        return row, tuple(aliases)

    @staticmethod
    def _asset_snapshot(
        connection: Connection,
        object_id: str,
        merchant: tuple[str, tuple[str, ...]] | None,
    ) -> CandidateSnapshot:
        row = (
            connection.execute(
                select(object_table.c.label, asset)
                .join(asset, asset.c.object_id == object_table.c.id)
                .where(object_table.c.id == object_id)
            )
            .mappings()
            .one()
        )
        aliases = merchant[1] if merchant else ()
        matched = {EvidenceCode.MERCHANT_ALIAS_EXACT: merchant[0]} if merchant else {}
        return CandidateSnapshot(
            object_id,
            "asset",
            row["label"],
            merchant_aliases=aliases,
            make=row["make"],
            model=row["model"],
            serial=row["serial"],
            order_reference=row["order_reference"],
            matched_object_ids=matched,
        )


class SqlEvidenceValidator:
    """Recompute persisted evidence inside the caller's write transaction."""

    def validate(self, connection: object, request: ValidationRequest) -> bool:
        conn = cast(Connection, connection)
        signal = conn.execute(
            select(extraction_signal.c.signal_type, extraction_signal.c.normalised_value).where(
                extraction_signal.c.id == request.signal_id,
                extraction_signal.c.document_id == request.document_id,
            )
        ).one_or_none()
        if signal is None:
            return False
        query = self._query(request, signal.signal_type, signal.normalised_value)
        return query is not None and conn.execute(query).first() is not None

    @staticmethod
    def _query(
        request: ValidationRequest, signal_type: str, value: str
    ) -> Select[tuple[str]] | None:
        if request.code == "address_exact" and signal_type == "property_address":
            if request.matched_object_id != request.candidate_object_id:
                return None
            return select(property_table.c.object_id).where(
                property_table.c.object_id == request.candidate_object_id,
                property_table.c.normalised_address == value,
            )
        if request.code == "account_suffix_exact" and signal_type == "account_suffix":
            return select(account.c.object_id).where(
                account.c.object_id == request.matched_object_id,
                account.c.related_subject_id == request.candidate_object_id,
                account.c.suffix == value,
            )
        if request.code == "issuer_exact" and signal_type == "issuer_name":
            return (
                select(account.c.object_id)
                .join(organisation, organisation.c.object_id == account.c.issuer_organisation_id)
                .where(
                    account.c.object_id == request.matched_object_id,
                    account.c.related_subject_id == request.candidate_object_id,
                    organisation.c.normalised_name == value,
                )
            )
        if request.code == "merchant_alias_exact" and signal_type == "merchant_name":
            return (
                select(organisation_alias.c.organisation_id)
                .join(
                    organisation,
                    organisation.c.object_id == organisation_alias.c.organisation_id,
                )
                .where(
                    organisation_alias.c.organisation_id == request.matched_object_id,
                    organisation_alias.c.normalised_alias == value,
                    select(asset.c.object_id)
                    .where(asset.c.object_id == request.candidate_object_id)
                    .exists(),
                )
            )
        field_by_code = {
            "model_exact": ("model", asset.c.model),
            "serial_exact": ("serial", asset.c.serial),
            "order_reference_exact": ("order_reference", asset.c.order_reference),
        }
        expected = field_by_code.get(request.code)
        if expected is None or signal_type != expected[0]:
            return None
        if request.matched_object_id != request.candidate_object_id:
            return None
        return select(asset.c.object_id).where(
            asset.c.object_id == request.candidate_object_id,
            expected[1] == value,
        )
