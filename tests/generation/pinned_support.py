"""The pinned fixtures of W1-15 (``tests/generation/pinned/``): a spec per built-in strategy (and
per distribution family), pinned with ``shape pin``, and ``expected.json``: for each spec the
dataset id of seed 42 at 200 rows per table, for each set of generator versions that was ever
released. ``docs/GENERATION_STABILITY.md`` describes the promise they enforce.

Add the id of a new version (never change an id that is there)::

    python tests/generation/pinned_support.py --write
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

PINNED = Path(__file__).parent / "pinned"
EXPECTED = PINNED / "expected.json"
SEED = 42
ROWS = 200
FORMAT = "generator-pinned-ids"
VERSION = 1

# The reference datasets the specs read (the same as ``spec_compat``).
DATASETS: dict[str, list[Any]] = {
    "compat_colors": ["red", "green", "blue"],
    "compat_people": [{"income": 10.0 * i} for i in range(1, 30)],
    "compat_places": [{"city": f"c{i}", "zip": f"{10000 + i}"} for i in range(40)],
}


def stems() -> list[str]:
    return sorted(p.stem for p in PINNED.glob("*.json") if p.name != EXPECTED.name)


def load_spec(stem: str) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads((PINNED / f"{stem}.json").read_text("utf-8"))
    return doc


def load_expected() -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(EXPECTED.read_text("utf-8"))
    return doc


@contextmanager
def datasets() -> Iterator[None]:
    from shape.generation import reference

    for name, rows in DATASETS.items():
        reference.register_dataset(name, rows)
    try:
        yield
    finally:
        for name in DATASETS:
            reference.unregister_dataset(name)


def engine(doc: Mapping[str, Any], generators: Mapping[str, int] | None = None) -> Any:
    """The engine for ``doc`` at seed 42 and 200 rows per table. ``generators`` replaces the spec's
    pins (``{}`` runs every generator at its latest version)."""
    from shape.generation.engine import Engine
    from shape.generation.schema import GenSchema

    schema = GenSchema.from_dict(dict(doc))
    if generators is not None:
        schema.generators = dict(generators)
    return Engine(schema, seed=SEED, row_counts={t: ROWS for t in schema.tables})


def dataset_id_of(doc: Mapping[str, Any], generators: Mapping[str, int] | None = None) -> str:
    from shape.repro import dataset_id

    return dataset_id(engine(doc, generators).generate().tables)


def write_missing() -> int:
    """Add an entry for every spec whose latest versions have none; return how many."""
    expected = load_expected()
    added = 0
    with datasets():
        for stem in stems():
            doc = load_spec(stem)
            latest = engine(doc, {}).generator_versions
            entries = expected["specs"].setdefault(stem, [])
            if not any(e["generators"] == latest for e in entries):
                entries.append({"generators": latest, "id": dataset_id_of(doc, latest)})
                added += 1
    expected["specs"] = dict(sorted(expected["specs"].items()))
    EXPECTED.write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")
    return added


if __name__ == "__main__":
    if sys.argv[1:] != ["--write"]:
        raise SystemExit(__doc__)
    if not EXPECTED.exists():
        EXPECTED.write_text(
            json.dumps(
                {"format": FORMAT, "version": VERSION, "seed": SEED, "rows": ROWS, "specs": {}},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    print(f"added {write_missing()} entries")
