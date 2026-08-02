from __future__ import annotations

import sys
from pathlib import Path

import pytest

from reversecrm.ingest.pipeline import BoundedTextExtractor, ProcessingError, _run_bounded


def test_text_extractor_rejects_unregistered_mime(tmp_path: Path) -> None:
    source = tmp_path / "document.txt"
    source.write_text("receipt")
    with pytest.raises(ProcessingError, match="unsupported_mime"):
        BoundedTextExtractor().extract(source, "text/plain")


def test_text_extractor_rejects_symlink(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF")
    link = tmp_path / "link.pdf"
    link.symlink_to(source)
    with pytest.raises(ProcessingError, match="unsafe_input"):
        BoundedTextExtractor().extract(link, "application/pdf")


def test_text_extractor_uses_argv_timeout_and_page_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF fixture")
    observed: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> bytes:
        observed["command"] = command
        observed.update(kwargs)
        return b"COUNCIL RATES NOTICE\n"

    monkeypatch.setattr("reversecrm.ingest.pipeline._run_bounded", fake_run)
    extractor = BoundedTextExtractor(timeout_seconds=3, max_pdf_pages=7)
    assert extractor.extract(source, "application/pdf").startswith("COUNCIL")
    assert observed["command"][:6] == ["pdftotext", "-f", "1", "-l", "7", "-enc"]
    assert observed["timeout_seconds"] == 3
    assert observed["max_bytes"] == extractor.max_text_bytes


def test_bounded_runner_stops_oversized_output() -> None:
    command = [sys.executable, "-c", "import sys; sys.stdout.write('x' * 1024)"]

    with pytest.raises(ProcessingError, match="text_size"):
        _run_bounded(command, env={}, max_bytes=32, timeout_seconds=2)


def test_bounded_runner_stops_timed_out_process() -> None:
    command = [sys.executable, "-c", "import time; time.sleep(5)"]

    with pytest.raises(ProcessingError, match="text_extraction_timeout"):
        _run_bounded(command, env={}, max_bytes=32, timeout_seconds=0.05)
