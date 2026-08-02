"""Bounded subprocess boundary for deterministic document text extraction."""

from __future__ import annotations

import os
import selectors
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

ALLOWED_MIME = frozenset({"application/pdf", "image/png", "image/jpeg", "image/tiff"})
MAX_INPUT_BYTES = 20 * 1024 * 1024
MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_PDF_PAGES = 25
DEFAULT_TIMEOUT_SECONDS = 60.0


class ProcessingError(RuntimeError):
    """Expected, non-sensitive processing failure with a stable error code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _run_bounded(
    command: list[str], *, env: dict[str, str], max_bytes: int, timeout_seconds: float
) -> bytes:
    """Run a fixed argv command while bounding stdout bytes and wall time."""

    try:
        process = subprocess.Popen(  # noqa: S603
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
        )
    except OSError as exc:
        raise ProcessingError("text_extractor_unavailable") from exc
    if process.stdout is None:  # pragma: no cover - guaranteed by stdout=PIPE
        process.kill()
        raise ProcessingError("text_extraction_failed")

    output = bytearray()
    deadline = time.monotonic() + timeout_seconds
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise ProcessingError("text_extraction_timeout")
            chunk = os.read(process.stdout.fileno(), min(64 * 1024, max_bytes + 1 - len(output)))
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > max_bytes:
                raise ProcessingError("text_size")
        try:
            return_code = process.wait(timeout=max(deadline - time.monotonic(), 0.001))
        except subprocess.TimeoutExpired as exc:
            raise ProcessingError("text_extraction_timeout") from exc
    finally:
        selector.close()
        process.stdout.close()
        if process.poll() is None:
            process.kill()
            process.wait()

    if return_code != 0:
        raise ProcessingError("text_extraction_failed")
    return bytes(output)


@dataclass(frozen=True, slots=True)
class BoundedTextExtractor:
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_input_bytes: int = MAX_INPUT_BYTES
    max_text_bytes: int = MAX_TEXT_BYTES
    max_pdf_pages: int = MAX_PDF_PAGES

    def extract(self, path: Path, mime: str) -> str:
        if mime not in ALLOWED_MIME:
            raise ProcessingError("unsupported_mime")
        try:
            stat = path.lstat()
        except OSError as exc:
            raise ProcessingError("input_unavailable") from exc
        if path.is_symlink() or not path.is_file():
            raise ProcessingError("unsafe_input")
        if stat.st_size <= 0 or stat.st_size > self.max_input_bytes:
            raise ProcessingError("input_size")

        if mime == "application/pdf":
            command = [
                "pdftotext",
                "-f",
                "1",
                "-l",
                str(self.max_pdf_pages),
                "-enc",
                "UTF-8",
                "--",
                str(path.resolve()),
                "-",
            ]
        else:
            command = ["tesseract", str(path.resolve()), "stdout", "-l", "eng"]
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"}
        stdout = _run_bounded(
            command,
            env=env,
            max_bytes=self.max_text_bytes,
            timeout_seconds=self.timeout_seconds,
        )
        try:
            text = stdout.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProcessingError("text_encoding") from exc
        if not text.strip():
            raise ProcessingError("empty_text")
        return text
