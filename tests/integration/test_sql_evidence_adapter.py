from __future__ import annotations

from pathlib import Path

from reversecrm.db import Database
from reversecrm.db.contracts import ExtractionSignalInput, ProposalEvidenceInput, ProposalInput
from reversecrm.db.services import PersistenceService
from reversecrm.domain.models import SignalType
from reversecrm.match.persistence import SqlCandidateReader, SqlEvidenceValidator


def test_multi_account_candidate_evidence_points_to_the_account_that_matched(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    persistence = PersistenceService(database)
    unrelated_issuer = persistence.create_organisation("Alpha Council", "ALPHA COUNCIL")
    matching_issuer = persistence.create_organisation("Zulu Bank", "ZULU BANK")
    subject = persistence.create_property("Home", "7 SAMPLE ROAD", "occupied")
    persistence.create_account("Rates", "rates", "1111", unrelated_issuer, subject)
    matching_account = persistence.create_account(
        "Mortgage", "mortgage", "7734", matching_issuer, subject
    )

    candidate = SqlCandidateReader(database).find_property_candidates(
        {
            SignalType.ISSUER_NAME: ("ZULU BANK",),
            SignalType.ACCOUNT_SUFFIX: ("7734",),
            SignalType.PROPERTY_ADDRESS: ("7 SAMPLE ROAD",),
        },
        limit=3,
    )[0]

    assert candidate.matched_object_ids
    assert set(candidate.matched_object_ids.values()) == {matching_account}


def test_canonical_merchant_name_is_not_treated_as_registered_alias(tmp_path: Path) -> None:
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    persistence = PersistenceService(database)
    persistence.create_organisation("Bright Home Pty Ltd", "BRIGHT HOME PTY LTD")
    persistence.create_asset("Fridge", model="PF-600X")

    candidates = SqlCandidateReader(database).find_asset_candidates(
        {
            SignalType.MERCHANT_NAME: ("BRIGHT HOME PTY LTD",),
            SignalType.MODEL: ("PF-600X",),
        },
        limit=3,
    )

    assert candidates
    assert candidates[0].merchant_aliases == ()


def test_real_validator_accepts_matching_multi_account_signal_ids(tmp_path: Path) -> None:
    """Exercise the persistence callback with IDs, not only domain snapshots."""

    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    persistence = PersistenceService(database)
    issuer = persistence.create_organisation("Zulu Bank", "ZULU BANK")
    subject = persistence.create_property("Home", "7 SAMPLE ROAD", "occupied")
    matching_account = persistence.create_account("Mortgage", "mortgage", "7734", issuer, subject)
    # A logical document is needed because extraction signals are provenance-bound.
    from reversecrm.db.contracts import EvidencePlacement

    document_id = persistence.ingest_placement(
        source_kind="test",
        source_identity="multi-account",
        placement=EvidencePlacement("a" * 64, 4, "application/pdf", f"aa/aa/{'a' * 64}"),
    ).document_id
    signal_id = persistence.store_extraction_signals(
        document_id,
        [
            ExtractionSignalInput(
                "account_suffix", "7734", "****7734", "line:4", "anchor", "anchor-v1"
            )
        ],
    )[0]
    ids = persistence.persist_proposals(
        document_id,
        [
            ProposalInput(
                subject,
                "concerns_property",
                50,
                (ProposalEvidenceInput("account_suffix_exact", signal_id, matching_account),),
            )
        ],
        SqlEvidenceValidator(),
    )
    assert len(ids) == 1
