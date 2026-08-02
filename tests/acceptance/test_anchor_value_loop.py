import json
from pathlib import Path
from typing import Any

import pytest

from reversecrm.fixtures import fixture_files

FIXTURES = Path(__file__).parents[1] / "fixtures"


def contracts() -> list[dict[str, Any]]:
    return [json.loads(path.read_text()) for path in sorted((FIXTURES / "expected").glob("*.json"))]


@pytest.mark.acceptance
@pytest.mark.parametrize("contract", contracts(), ids=lambda item: item["fixture"])
def test_frozen_anchor_contract(value_loop: Any, contract: dict[str, Any]) -> None:
    document_bytes = fixture_files()[contract["fixture"]]
    result = value_loop.submit_and_process(
        content=document_bytes,
        filename=contract["fixture"],
        source_identity=contract["source_identity"],
        network_enabled=False,
    )

    assert len(result.proposals) <= 3
    assert result.proposals[0].target_id == contract["expected_target_id"]
    assert result.proposals[0].predicate == contract["predicate"]
    assert set(contract["required_evidence_codes"]) <= set(result.proposals[0].evidence_codes)
    assert result.extracted_signals == contract["signals"]
    assert result.all_evidence_revalidates()
    assert result.created_subject_ids == []

    forbidden_first = contract.get("forbidden_first_target_id")
    assert forbidden_first is None or result.proposals[0].target_id != forbidden_first
    forbidden_predicates = set(contract.get("forbidden_predicates", []))
    assert forbidden_predicates.isdisjoint(result.persisted_predicates)

    confirmation = value_loop.confirm(
        result.proposals[0].id,
        reviewer="acceptance-reviewer",
        idempotency_key=f"confirm:{contract['source_identity']}",
    )
    related = value_loop.related_documents(contract["expected_target_id"])
    assert confirmation.relationship_id in {item.relationship_id for item in related}
    assert result.document_id in {item.document_id for item in related}
    assert all(item.evidence_codes for item in related)


@pytest.mark.acceptance
@pytest.mark.parametrize("contract", contracts(), ids=lambda item: item["fixture"])
def test_submission_and_confirmation_are_idempotent(
    value_loop: Any, contract: dict[str, Any]
) -> None:
    content = fixture_files()[contract["fixture"]]
    first = value_loop.submit_and_process(
        content=content,
        filename=contract["fixture"],
        source_identity=contract["source_identity"],
        network_enabled=False,
    )
    replay = value_loop.submit_and_process(
        content=content,
        filename=contract["fixture"],
        source_identity=contract["source_identity"],
        network_enabled=False,
    )
    assert replay.persistence_counts == first.persistence_counts

    key = f"confirm:{contract['source_identity']}"
    decision = value_loop.confirm(first.proposals[0].id, "acceptance-reviewer", key)
    repeat = value_loop.confirm(first.proposals[0].id, "acceptance-reviewer", key)
    assert repeat == decision

    distinct_source = value_loop.submit_and_process(
        content=content,
        filename=contract["fixture"],
        source_identity=f"{contract['source_identity']}:second-source",
        network_enabled=False,
    )
    assert distinct_source.blob_id == first.blob_id
    assert distinct_source.document_id == first.document_id
    assert (
        distinct_source.persistence_counts.source_references
        == first.persistence_counts.source_references + 1
    )
    assert distinct_source.persistence_counts.proposals == first.persistence_counts.proposals
