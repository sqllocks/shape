"""Shared definitions of the scenario-pack parity harness (P6-14): the reference inputs, the
Shape-name to baseline-name mapping (D-13) and the named allow-list of intentional differences.

Standard library only: imported from both the baseline venv and the Shape venv.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parents[2] / "tests" / "fixtures" / "packs"

# The pack scale and seeds. The reference input runs at fabric_demo, seed 42 (the baseline) and
# 1042 (Shape), with the baseline's seeds 43-46 as its own spread: the seed set of T-21.
SCALE = "fabric_demo"
REF_SEED = 42
BASELINE_SEEDS = (43, 44, 45, 46)
SHAPE_SEED = 1042
DOMAIN = "retail"

# (file name, kind). A spec's `scenario.pack` points at the tutorial pack.
INPUTS: tuple[tuple[str, str], ...] = (
    ("tutorial_custom_pack.yaml", "pack"),
    ("notebook_custom_pack.yaml", "pack"),
    ("retail_basic.gsl.yaml", "spec"),
    ("retail_chaos.gsl.yaml", "spec"),
    ("retail_hybrid.gsl.yaml", "spec"),
)

# D-13: Shape's neutral names and the baseline names they stand for. The harness maps a Shape
# name back to the baseline's before it compares (a manifest sbom key, a GSL schema type).
NAME_MAP_BACK: dict[str, str] = {
    "sqllocks-shape": "sqllocks-spindle",  # run manifest: sbom key
    "schema_file": "spindle_json",  # GSL schema.type
}

# Fields only Shape has, or only the baseline has, in the parsed structure of a pack or spec.
SHAPE_ONLY_FIELDS = {"extra_keys", "path", "outputs.eventstream.topic_prefix"}
BASELINE_ONLY_FIELDS = {"_base_dir"}

# Trust-harming defects of the baseline that Shape fixes (owner's standing decision, 2026-10-01).
# Each is a narrow, named exception; anything else that differs fails the harness. `observed`
# entries are exercised by the reference inputs; the others are covered by
# tests/scenario/test_defects.py (the reference inputs do not trigger them).
ALLOWED: dict[str, dict[str, str]] = {
    "PK-1": {
        "what": "manifest tables.*.file_paths",
        "baseline": "a table lists every written file whose path contains the table's name "
        "('order' lists order_line's file, a directory named like a table matches all)",
        "shape": "a table lists exactly its own files",
    },
    "PK-2": {
        "what": "manifest spec_hash",
        "baseline": "always empty (it hashes the spec's directory, never the file)",
        "shape": "the sha256 of the spec file when the run came from a spec",
    },
    "PK-3": {
        "what": "manifest outputs",
        "baseline": "always empty",
        "shape": "kind, file and event counts and the output root",
    },
    "PK-4": {
        "what": "unknown validation gate / failing gate",
        "baseline": "an unknown gate passes; a failed gate still reports SUCCESS",
        "shape": "an unknown gate fails; a failed gate (without chaos) fails the run",
        "observed": "no",
    },
    "PK-5": {
        "what": "landing and topic paths",
        "baseline": "an absolute or '..' lakehouse_files_root or topic name writes outside the "
        "output directory",
        "shape": "rejected by validation",
        "observed": "no",
    },
    "PK-6": {
        "what": "malformed documents",
        "baseline": "an empty or non-mapping file, or a wrong-typed value, crashes or is "
        "silently misread",
        "shape": "a clear error naming the key",
        "observed": "no",
    },
    "PK-7": {
        "what": "pack chaos section",
        "baseline": "parsed and ignored by the runner",
        "shape": "applied with shape.chaos; counts per category in manifest chaos",
        "observed": "no",
    },
    "PK-8": {
        "what": "extra warnings",
        "baseline": "unknown keys, unsimulated enabled features and topics matching no table "
        "pass silently",
        "shape": "a warning each",
        "observed": "no",
    },
    "PK-9": {
        "what": "stream files: datetimes",
        "baseline": "pandas to_json writes datetimes as epoch milliseconds",
        "shape": "ISO 8601",
        "observed": "no",
    },
    "ID-1": {
        "what": "manifest engine_version, sbom values, timestamps, run_id",
        "baseline": "the baseline's version, packages and clock",
        "shape": "Shape's own",
    },
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    """A nested mapping/list as ``{dotted.path: leaf}`` (lists index as ``[i]``)."""
    out: dict[str, Any] = {}
    if isinstance(value, dict):
        for k, v in value.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(value, list):
        if not value:
            out[prefix] = []
        for i, v in enumerate(value):
            out.update(flatten(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = value
    return out


def manifest_key_paths(manifest: dict[str, Any], literal: bool = False) -> set[str]:
    """Every key path of a run manifest, a table name replaced by ``*``. The names under
    ``validation`` (gates) and ``chaos`` (categories) are data, so they become ``*`` too unless
    ``literal`` is set."""
    paths: set[str] = set()
    for key, value in manifest.items():
        paths.add(key)
        if not isinstance(value, dict):
            continue
        if key == "tables":
            paths.add("tables/*")
            for table in value.values():
                if isinstance(table, dict):
                    paths.update(f"tables/*/{sub}" for sub in table)
        elif key in ("validation", "chaos") and not literal:
            if value:
                paths.add(f"{key}/*")
        else:
            paths.update(f"{key}/{sub}" for sub in value)
    return paths


def dumps(obj: Any) -> str:
    return json.dumps(obj, indent=1, sort_keys=True, default=str)
