"""A single-purpose, bounded processing worker.

Claiming and durable state transitions belong to persistence. Expensive file
inspection, text extraction, and matching happen outside database transactions.
"""

from __future__ import annotations

import os
import subprocess
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
        try:
            # Fixed executable/flags, argv-only invocation, bounded input and timeout.
            completed = subprocess.run(  # noqa: S603
                command,
                check=False,
                capture_output=True,
                timeout=self.timeout_seconds,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise ProcessingError("text_extraction_timeout") from exc
        except OSError as exc:
            raise ProcessingError("text_extractor_unavailable") from exc
        if completed.returncode != 0:
            raise ProcessingError("text_extraction_failed")
        if len(completed.stdout) > self.max_text_bytes:
            raise ProcessingError("text_size")
        try:
            text = completed.stdout.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProcessingError("text_encoding") from exc
        if not text.strip():
            raise ProcessingError("empty_text")
        return text
