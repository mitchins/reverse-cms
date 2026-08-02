"""Immutable, content-addressed evidence storage."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import BinaryIO

from reversecrm.db.contracts import EvidencePlacement


class EvidenceStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.incoming = self.root / ".incoming"
        self.incoming.mkdir(parents=True, exist_ok=True)

    def place(
        self,
        stream: BinaryIO,
        detected_mime: str,
        *,
        max_bytes: int | None = None,
        chunk_size: int = 1024 * 1024,
    ) -> EvidencePlacement:
        """Stream, hash, fsync and atomically make bytes visible by digest."""
        digest = hashlib.sha256()
        byte_size = 0
        fd, temporary_name = tempfile.mkstemp(prefix="evidence-", dir=self.incoming)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as output:
                while chunk := stream.read(chunk_size):
                    byte_size += len(chunk)
                    if max_bytes is not None and byte_size > max_bytes:
                        raise ValueError("evidence exceeds configured byte limit")
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())

            sha256 = digest.hexdigest()
            relative = Path(sha256[:2]) / sha256[2:4] / sha256
            destination = self.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(temporary, destination)
            except FileExistsError:
                if destination.stat().st_size != byte_size:
                    raise RuntimeError(
                        "content-address collision or corrupt existing evidence"
                    ) from None
            self._fsync_directory(destination.parent)
            return EvidencePlacement(sha256, byte_size, detected_mime, relative.as_posix())
        finally:
            temporary.unlink(missing_ok=True)

    def resolve(self, relative_path: str) -> Path:
        candidate = (self.root / relative_path).resolve()
        root = self.root.resolve()
        if not candidate.is_relative_to(root):
            raise ValueError("evidence path escapes store root")
        return candidate

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
