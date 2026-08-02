from __future__ import annotations

from collections.abc import Mapping, Sequence

import pytest

from reversecrm.domain.evidence import registry_codes, validate_evidence
from reversecrm.domain.models import CandidateSnapshot, DocumentKind, EvidenceCode, SignalType
from reversecrm.domain.normalise import normalise_identifier, normalise_text
from reversecrm.extract.anchors import extract_anchor
from reversecrm.match.service import MAX_CANDIDATES, build_proposals


class CandidateFake:
    def __init__(
        self,
        *,
        properties: Sequence[CandidateSnapshot] = (),
        assets: Sequence[CandidateSnapshot] = (),
    ) -> None:
        self.properties = properties
        self.assets = assets
        self.requested_limits: list[int] = []

    def find_property_candidates(
        self, signals: Mapping[SignalType, tuple[str, ...]], *, limit: int
    ) -> Sequence[CandidateSnapshot]:
        self.requested_limits.append(limit)
        return self.properties[:limit]

    def find_asset_candidates(
        self, signals: Mapping[SignalType, tuple[str, ...]], *, limit: int
    ) -> Sequence[CandidateSnapshot]:
        self.requested_limits.append(limit)
        return self.assets[:limit]


def property_candidate(object_id: str, address: str, issuer: str, suffix: str) -> CandidateSnapshot:
    return CandidateSnapshot(
        object_id,
        "property",
        object_id,
        address=normalise_text(address),
        account_suffixes=(suffix,),
        issuer_names=(normalise_text(issuer),),
        matched_object_ids={
            EvidenceCode.ISSUER_EXACT: f"account-{object_id}",
            EvidenceCode.ACCOUNT_SUFFIX_EXACT: f"account-{object_id}",
        },
    )


@pytest.mark.parametrize(
    ("text", "expected_kind", "expected_id", "expected_codes"),
    [
        (
            """COUNCIL RATES NOTICE
Issuer: Lakeview Council
Property Address: 18 Harbour Street, Newcastle NSW 2300
Account Suffix: 4821
""",
            DocumentKind.COUNCIL_RATES,
            "property-rental",
            {"issuer_exact", "address_exact", "account_suffix_exact"},
        ),
        (
            """MORTGAGE STATEMENT
Lender: Southern Mutual Bank
Mortgaged Property: 7 Home Road, Sydney NSW 2000
Loan Account: ****7784
""",
            DocumentKind.MORTGAGE_STATEMENT,
            "property-home",
            {"issuer_exact", "address_exact", "account_suffix_exact"},
        ),
    ],
)
def test_property_anchors_rank_expected_existing_property(
    text: str, expected_kind: DocumentKind, expected_id: str, expected_codes: set[str]
) -> None:
    extracted = extract_anchor(text)
    candidates = CandidateFake(
        properties=(
            property_candidate(
                expected_id,
                "18 Harbour Street, Newcastle NSW 2300"
                if "rental" in expected_id
                else "7 Home Road, Sydney NSW 2000",
                "Lakeview Council" if "rental" in expected_id else "Southern Mutual Bank",
                "4821" if "rental" in expected_id else "7784",
            ),
            property_candidate(
                "property-distractor", "99 Elsewhere Avenue", "Different Issuer", "0000"
            ),
        )
    )
    proposals = build_proposals(extracted.document_kind, extracted.signals, candidates)

    assert extracted.document_kind is expected_kind
    assert proposals[0].candidate.object_id == expected_id
    assert {str(item.code) for item in proposals[0].evidence} == expected_codes
    assert str(proposals[0].predicate) == "concerns_property"
    assert candidates.requested_limits == [MAX_CANDIDATES]


def test_fridge_receipt_matches_existing_asset_with_closed_evidence() -> None:
    text = """TAX INVOICE / RECEIPT
Merchant: Harbour Appliances
Purchase Date: 18/07/2026
Total: $1,249.00
Make: Frostline
Model: CHILL-500
Serial Number: FR-500-99881
Order Reference: ORD-55291
"""
    extracted = extract_anchor(text)
    fridge = CandidateSnapshot(
        "asset-fridge",
        "asset",
        "Kitchen fridge",
        merchant_aliases=(normalise_text("Harbour Appliances"),),
        make=normalise_text("Frostline"),
        model=normalise_identifier("CHILL-500"),
        serial=normalise_identifier("FR-500-99881"),
        order_reference=normalise_identifier("ORD-55291"),
        matched_object_ids={EvidenceCode.MERCHANT_ALIAS_EXACT: "organisation-merchant"},
    )
    distractor = CandidateSnapshot(
        "asset-dishwasher",
        "asset",
        "Dishwasher",
        merchant_aliases=(normalise_text("Harbour Appliances"),),
        model=normalise_identifier("WASH-90"),
        serial=normalise_identifier("DW-100"),
    )
    candidates = CandidateFake(assets=(distractor, fridge))

    proposals = build_proposals(extracted.document_kind, extracted.signals, candidates)

    assert extracted.document_kind is DocumentKind.PURCHASE_RECEIPT
    assert proposals[0].candidate.object_id == "asset-fridge"
    assert str(proposals[0].predicate) == "purchase_evidence_for"
    assert {str(item.code) for item in proposals[0].evidence} == {
        "merchant_alias_exact",
        "model_exact",
        "serial_exact",
        "order_reference_exact",
    }
    assert proposals[0].evidence[0].code in registry_codes()


def test_item_fallback_displays_the_exact_make_and_model_tokens() -> None:
    extracted = extract_anchor(
        "TAX INVOICE\nMerchant: Example Store\nItem: Polaris PF-600X Refrigerator\n"
    )
    displays = {signal.signal_type: signal.display_value for signal in extracted.signals}

    assert displays[SignalType.MAKE] == "Polaris"
    assert displays[SignalType.MODEL] == "PF-600X"


def test_issuer_or_merchant_alone_cannot_create_a_proposal() -> None:
    extracted = extract_anchor("TAX INVOICE / RECEIPT\nMerchant: Harbour Appliances\n")
    merchant_only = CandidateSnapshot(
        "asset-unknown",
        "asset",
        "Unknown appliance",
        merchant_aliases=(normalise_text("Harbour Appliances"),),
    )
    assert (
        build_proposals(
            extracted.document_kind, extracted.signals, CandidateFake(assets=(merchant_only,))
        )
        == ()
    )


def test_evidence_validator_rejects_claim_not_supported_by_signal() -> None:
    extracted = extract_anchor(
        "COUNCIL RATES NOTICE\nIssuer: Lakeview Council\nProperty Address: 18 Harbour Street\n"
    )
    address_signal = next(
        item for item in extracted.signals if item.signal_type is SignalType.PROPERTY_ADDRESS
    )
    candidate = property_candidate("property-rental", "99 Wrong Street", "Lakeview Council", "4821")
    with pytest.raises(ValueError, match="not supported"):
        validate_evidence(EvidenceCode.ADDRESS_EXACT, address_signal, candidate)


def test_reader_cannot_return_more_than_bound() -> None:
    class BrokenReader(CandidateFake):
        def find_asset_candidates(
            self, signals: Mapping[SignalType, tuple[str, ...]], *, limit: int
        ) -> Sequence[CandidateSnapshot]:
            return [
                CandidateSnapshot(str(index), "asset", str(index)) for index in range(limit + 1)
            ]

    extracted = extract_anchor("RECEIPT\nModel: CHILL-500\n")
    with pytest.raises(ValueError, match="bounded retrieval"):
        build_proposals(extracted.document_kind, extracted.signals, BrokenReader())
