"""Helpers of the ``shape demo`` tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

SCALE_TESTS = str(Path(__file__).resolve().parents[1] / "scale")
if SCALE_TESTS not in sys.path:
    sys.path.insert(0, SCALE_TESTS)

from scale_schemas import plain_doc  # noqa: E402

ROWS = {"customer": 40, "order": 1200, "order_line": 3100}


def write_schema(path: Path, rows: dict[str, int] | None = None, name: str | None = None) -> Path:
    doc = plain_doc(rows or ROWS)
    if name:
        doc["model"]["domain"] = name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


class FakeServices:
    """Stands in for the remote services: records every call, fails on request."""

    def __init__(self, fail: tuple[str, ...] = ()) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.fail = set(fail)

    def _do(self, *call: Any) -> None:
        self.calls.append(call)
        if any(str(part).rsplit("/", 1)[-1] in self.fail for part in call):
            raise RuntimeError(f"{call[0]} failed for {call[-1]}")

    def drop_sql_table(self, target: str, schema_name: str, table: str) -> None:
        self._do("drop_sql_table", target, schema_name, table)

    def drop_kql_table(self, table: str) -> None:
        self._do("drop_kql_table", table)

    def remove_files(self, path: str) -> None:
        self._do("remove_files", path)

    def check(self, target: str) -> str:
        self._do("check", target)
        return f"{target} answered"
