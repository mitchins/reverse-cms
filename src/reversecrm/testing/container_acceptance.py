"""Run the frozen anchor loop inside the network-disabled runtime image."""

from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path

from reversecrm.fixtures import fixture_files
from reversecrm.ingest.pipeline import BoundedTextExtractor
from reversecrm.ingest.worker import WorkerApplication, run_once

from .acceptance import AcceptanceDriver

EXPECTED = {
    "rental-council-rates.pdf": "property-rental",
    "occupied-home-mortgage.pdf": "property-occupied-home",
    "fridge-receipt.pdf": "asset-fridge",
}


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m reversecrm.testing.container_acceptance SEED_JSON")
    with (
        tempfile.TemporaryDirectory(prefix="reversecrm-acceptance-") as directory,
        AcceptanceDriver(Path(directory)) as driver,
    ):
        driver.seed(sys.argv[1])
        submissions: dict[str, str] = {}
        for fixture in EXPECTED:
            receipt = driver.application.submit(
                content=fixture_files()[fixture],
                filename=fixture,
                source_identity=f"container:{fixture}",
                max_bytes=20 * 1024 * 1024,
            )
            submissions[fixture] = receipt.document_id

        worker = WorkerApplication(
            driver.database,
            driver.application,
            BoundedTextExtractor(),
            lease_seconds=120,
        )
        logger = logging.getLogger("reversecrm.container_acceptance")
        for _fixture in EXPECTED:
            if not run_once(worker, worker_id="container-worker", logger=logger):
                raise RuntimeError("queue became idle before all anchor jobs completed")
        if run_once(worker, worker_id="container-worker", logger=logger):
            raise RuntimeError("queue contained an unexpected fourth processing job")

        for fixture, expected_target in EXPECTED.items():
            document_id = submissions[fixture]
            proposals = driver.application.proposals(document_id)
            proposal = proposals[0]
            if (
                proposal.target_id != expected_target
                or not driver.application.all_evidence_revalidates(document_id)
            ):
                raise RuntimeError(f"container acceptance failed for {fixture}")
            confirmation = driver.confirm(
                proposal.id,
                reviewer="container-acceptance",
                idempotency_key=f"container-confirm:{fixture}",
            )
            related = driver.related_documents(expected_target)
            if confirmation.relationship_id not in {item.relationship_id for item in related}:
                raise RuntimeError(f"relationship query failed for {fixture}")
    print("container acceptance passed: three anchor relationships confirmed and queried")


if __name__ == "__main__":
    main()
