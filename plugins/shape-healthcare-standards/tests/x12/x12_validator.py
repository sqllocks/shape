"""Test-only wrapper around the open-source ``pyx12`` validator and reader."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any

import pyx12
import pyx12.params
import pyx12.x12file
import pyx12.x12n_document

MAP_DIR = os.path.join(os.path.dirname(pyx12.__file__), "map")


def _collect(node: Any, found: list[str]) -> None:
    if isinstance(node, dict):
        for err in node.get("errors") or []:
            found.append(f"{err.get('err_cde')}: {err.get('err_str')}")
        for value in node.values():
            _collect(value, found)
    elif isinstance(node, list):
        for item in node:
            _collect(item, found)


def validate(path: str | Path) -> tuple[bool, list[str]]:
    """Validate a file against pyx12's implementation-guide map. ``(ok, error messages)``."""
    params = pyx12.params.ParamsBase()
    params.set("map_path", MAP_DIR)
    params.set("exclude_external_codes", None)
    out = io.StringIO()
    ok = pyx12.x12n_document.x12n_document(params, str(path), None, None, None, fd_json=out)
    errors: list[str] = []
    text = out.getvalue()
    if text:
        _collect(json.loads(text), errors)
    return bool(ok), errors


def segments(path: str | Path) -> list[Any]:
    """Every segment of a file, in order, as pyx12 segment objects."""
    return list(pyx12.x12file.X12Reader(str(path)))


def values(path: str | Path, seg_id: str, ref: str) -> list[str]:
    """The value of one element (e.g. ``CLM01``) in every ``seg_id`` segment."""
    return [s.get_value(ref) for s in segments(path) if s.get_seg_id() == seg_id]
