"""The time capsule: every file ever written for a persisted kind and format version still loads,
and gives the canonical form recorded when the file was written (W1-01, issue 55).

A generation under ``corpus/`` is frozen: its files are checked against the digests in its
``index.json``, and ``expected/<generation>/<id>.json`` holds what the reader returned for each.
Nothing here is edited to make a release pass; a reader that no longer matches is the defect.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import generate  # noqa: E402
from loaders import LOADERS, canonical_of  # noqa: E402

CORPUS = HERE / "corpus"
EXPECTED = HERE / "expected"


def _generations() -> list[Path]:
    return sorted(p for p in CORPUS.iterdir() if (p / "index.json").is_file())


def _entries() -> list[tuple[Path, dict]]:
    return [
        (g, e) for g in _generations() for e in json.loads((g / "index.json").read_text())["files"]
    ]


ENTRIES = _entries()
IDS = [f"{g.name}/{e['id']}" for g, e in ENTRIES]


def test_there_is_a_corpus() -> None:
    assert ENTRIES, "tests/timecapsule/corpus has no generation"
    assert (CORPUS / "base" / "index.json").is_file()


@pytest.mark.parametrize("generation", _generations(), ids=lambda g: g.name)
def test_generation_is_frozen(generation: Path) -> None:
    """Every file is listed with its digest, matches it, and nothing else is in the folder."""
    index = json.loads((generation / "index.json").read_text())
    listed = set()
    for e in index["files"]:
        path = generation / e["path"]
        assert path.exists(), f"{e['id']}: {e['path']} is missing"
        assert generate._tree_digest(path) == e["sha256"], f"{e['id']} was modified"
        listed.add(e["path"])
        assert (EXPECTED / generation.name / f"{e['id']}.json").is_file(), f"{e['id']}: no expected"
    support = {"index.json", "artifact/trusted.pub", "signature/manifest-v1.json"}
    support |= {"pack/tutorial_custom_pack.yaml"}
    files = {p.relative_to(generation).as_posix() for p in generation.rglob("*") if p.is_file()}
    owned = {
        f for f in files if any(f == x or f.startswith(x.rstrip("/") + "/") for x in listed)
    } | support
    assert files <= owned, f"unlisted files in {generation.name}: {sorted(files - owned)}"


@pytest.mark.parametrize(("generation", "entry"), ENTRIES, ids=IDS)
def test_file_loads_to_its_canonical_form(generation: Path, entry: dict) -> None:
    expected = (EXPECTED / generation.name / f"{entry['id']}.json").read_text(encoding="utf-8")
    assert canonical_of(generation, entry) == expected.rstrip("\n")


@pytest.mark.parametrize(("generation", "entry"), ENTRIES, ids=IDS)
def test_loading_never_modifies_the_corpus(generation: Path, entry: dict) -> None:
    before = generate._tree_digest(generation / entry["path"])
    canonical_of(generation, entry)
    assert generate._tree_digest(generation / entry["path"]) == before


def test_every_kind_has_a_loader() -> None:
    kinds = {e["kind"] for _, e in ENTRIES}
    assert kinds <= set(LOADERS), sorted(kinds - set(LOADERS))
