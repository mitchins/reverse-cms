import hashlib
import json
from pathlib import Path

from reversecrm.fixtures import FIXTURE_TEXT, fixture_files, generate_fixture_bundle

FIXTURES = Path(__file__).parent / "fixtures"


def test_fixture_bundle_is_deterministic_and_valid_pdf() -> None:
    first = fixture_files()
    second = fixture_files()

    assert first == second
    assert set(FIXTURE_TEXT) == {
        "rental-council-rates.pdf",
        "occupied-home-mortgage.pdf",
        "fridge-receipt.pdf",
    }
    assert all(first[name].startswith(b"%PDF-1.4") for name in FIXTURE_TEXT)
    assert all(first[name].endswith(b"%%EOF\n") for name in FIXTURE_TEXT)


def test_manifest_describes_generated_bytes() -> None:
    bundle = fixture_files()
    manifest = json.loads(bundle["manifest.json"])

    for name, metadata in manifest["documents"].items():
        assert metadata == {
            "sha256": hashlib.sha256(bundle[name]).hexdigest(),
            "size": len(bundle[name]),
        }


def test_generate_and_check_bundle(tmp_path: Path) -> None:
    assert generate_fixture_bundle(tmp_path) is False
    assert generate_fixture_bundle(tmp_path, check=True) is False

    (tmp_path / "fridge-receipt.pdf").write_bytes(b"stale")
    assert generate_fixture_bundle(tmp_path, check=True) is True


def test_acceptance_contracts_use_only_synthetic_seed_ids() -> None:
    seed = json.loads((FIXTURES / "seed.json").read_text())
    known_ids = {
        item["id"]
        for collection in ("organisations", "properties", "accounts", "assets")
        for item in seed[collection]
    }

    for contract_path in (FIXTURES / "expected").glob("*.json"):
        contract = json.loads(contract_path.read_text())
        assert contract["expected_target_id"] in known_ids
        assert contract["fixture"] in FIXTURE_TEXT
