"""Data or a profile: the two things a reference or a training set can be (W3-11)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]


def is_profile(obj: Any) -> bool:
    """True for a Shape profile (``shape.load``), or a profile-engine document, and not for
    a mapping of Arrow tables."""
    if hasattr(obj, "is_dataset") and hasattr(obj, "tables"):
        return True
    return (
        isinstance(obj, dict)
        and isinstance(obj.get("tables"), dict)
        and not any(isinstance(v, pa.Table) for v in obj.values())
    )


def load_data_or_profile(path: str | Path, fmt: str = "auto") -> dict[str, pa.Table] | Any:
    """Arrow tables keyed by file stem for data (a file, or a directory of files), or the profile
    for a ``.shape`` artifact or a profile-engine JSON document."""
    from .verify import load_tables

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Path not found: {path}")
    if p.is_file() and p.suffix == ".shape":
        import shape

        return shape.load(str(p))
    if p.is_file() and p.suffix == ".json":
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ValueError(f"{p} is not valid JSON: {exc}") from exc
        if not is_profile(doc):
            raise ValueError(
                f"{p} is JSON but not a profile (it has no tables); give data or a profile"
            )
        return doc
    tables = load_tables(p, fmt)
    if not tables:
        raise ValueError(f"no data files found in {path}")
    return tables
