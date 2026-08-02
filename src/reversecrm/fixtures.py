"""Deterministic generation of synthetic, non-household acceptance documents."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

FIXTURE_TEXT: Mapping[str, tuple[str, ...]] = {
    "rental-council-rates.pdf": (
        "NORTHSTAR CITY COUNCIL",
        "COUNCIL RATES NOTICE",
        "Service property: 18 EXAMPLE STREET, NORTHSTAR NSW 2000",
        "Assessment account: 0000-4821",
        "Notice date: 15 July 2026",
        "Amount due: $1,245.60",
        "SYNTHETIC ACCEPTANCE FIXTURE - NOT A REAL ACCOUNT",
    ),
    "occupied-home-mortgage.pdf": (
        "HARBORLIGHT MUTUAL BANK",
        "MORTGAGE STATEMENT",
        "Mortgaged property: 7 SAMPLE AVENUE, HARBOR NSW 2001",
        "Mortgage account: XXXX-7734",
        "Statement date: 30 June 2026",
        "Closing balance: $456,789.12",
        "SYNTHETIC ACCEPTANCE FIXTURE - NOT A REAL ACCOUNT",
    ),
    "fridge-receipt.pdf": (
        "BRIGHT HOME APPLIANCES",
        "PURCHASE RECEIPT",
        "Purchase date: 2 August 2026",
        "Amount: $2,499.00",
        "Item: POLARIS PF-600X REFRIGERATOR",
        "Serial: SYN-FRIDGE-90017",
        "Order reference: BHA-260802-441",
        "SYNTHETIC ACCEPTANCE FIXTURE - NOT A REAL PURCHASE",
    ),
}


def _escape_pdf_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _minimal_pdf(lines: tuple[str, ...]) -> bytes:
    commands = ["BT", "/F1 12 Tf", "72 760 Td", "16 TL"]
    for index, line in enumerate(lines):
        if index:
            commands.append("T*")
        commands.append(f"({_escape_pdf_text(line)}) Tj")
    commands.append("ET")
    stream = "\n".join(commands).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        (
            b"<< /Length "
            + str(len(stream)).encode("ascii")
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    document = bytearray(b"%PDF-1.4\n% synthetic reverse-crm fixture\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(document))
        document.extend(f"{number} 0 obj\n".encode("ascii"))
        document.extend(body)
        document.extend(b"\nendobj\n")
    xref_offset = len(document)
    document.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    document.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        document.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    trailer = (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
    )
    document.extend(trailer.encode("ascii"))
    return bytes(document)


def fixture_files() -> dict[str, bytes]:
    """Return the complete deterministic fixture bundle."""
    documents = {name: _minimal_pdf(lines) for name, lines in FIXTURE_TEXT.items()}
    manifest = {
        "format_version": 1,
        "documents": {
            name: {"sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}
            for name, content in sorted(documents.items())
        },
    }
    documents["manifest.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    return documents


def generate_fixture_bundle(output: Path, *, check: bool = False) -> bool:
    """Write fixtures, or report whether an existing fixture bundle differs."""
    expected = fixture_files()
    if check:
        return any(
            not (output / name).is_file() or (output / name).read_bytes() != data
            for name, data in expected.items()
        )
    output.mkdir(parents=True, exist_ok=True)
    for name, data in expected.items():
        (output / name).write_bytes(data)
    return False
