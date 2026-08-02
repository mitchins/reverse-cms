import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from reversecrm.testing.acceptance import AcceptanceDriver


@pytest.fixture
def value_loop(tmp_path: Any) -> Iterator[Any]:
    """Exercise the integrated SQLite/evidence core, never a test double."""
    driver = AcceptanceDriver(tmp_path)
    with driver:
        driver.seed(json.loads(Path("tests/fixtures/seed.json").read_text(encoding="utf-8")))
        yield driver
