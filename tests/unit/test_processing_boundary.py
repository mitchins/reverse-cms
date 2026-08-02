from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from reversecrm.ingest.pipeline import BoundedTextExtractor, ProcessingError


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

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed["command"] = command
        observed.update(kwargs)
        return subprocess.CompletedProcess(command, 0, b"COUNCIL RATES NOTICE\n", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    extractor = BoundedTextExtractor(timeout_seconds=3, max_pdf_pages=7)
    assert extractor.extract(source, "application/pdf").startswith("COUNCIL")
    assert observed["command"][:6] == ["pdftotext", "-f", "1", "-l", "7", "-enc"]
    assert observed["timeout"] == 3
    assert observed["capture_output"] is True
    assert "shell" not in observed
