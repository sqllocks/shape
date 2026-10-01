"""Example Shape plugin: a ``lines://`` source, an IBAN detector and ``shape hello``."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

import pyarrow as pa

SHAPE_API = "1.0"

_IBAN = re.compile(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}")


class LinesSource:
    """Reads a text file (``lines:///path``) as a one-column table named ``line``."""

    name = "lines"
    schemes = ("lines",)

    def can_open(self, uri: str) -> bool:
        return uri.startswith("lines://")

    def schema(self, uri: str, **options: Any) -> pa.Schema:
        return pa.schema([("line", pa.string())])

    def read(self, uri: str, **options: Any) -> Iterator[pa.RecordBatch]:
        path = uri.removeprefix("lines://")
        with open(path, encoding="utf-8") as fh:
            rows = [ln.rstrip("\n") for ln in fh]
        yield pa.RecordBatch.from_pydict({"line": rows}, schema=self.schema(uri))


class IbanDetector:
    """Labels a column ``iban`` when most of its values look like IBANs."""

    name = "iban"

    def detect(self, values: pa.Array, column: str) -> Any:
        from shape.plugins.api.v1 import Detection

        vals = [v for v in values.to_pylist() if v is not None]
        if not vals:
            return None
        share = sum(bool(_IBAN.fullmatch(str(v))) for v in vals) / len(vals)
        return Detection("iban", share) if share >= 0.8 else None


class HelloCommand:
    """``shape hello [--name NAME]``: prints a greeting."""

    name = "hello"
    help = "print a greeting (example plugin command)"

    def configure(self, parser: Any) -> None:
        parser.add_argument("--name", default="world")

    def run(self, args: Any) -> int:
        print(f"hello, {args.name}")
        return 0
