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

import capsule_generate as generate  # noqa: E402
from capsule_loaders import LOADERS, canonical_of  # noqa: E402

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
    support = {
        "index.json",
        "artifact/trusted.pub",
        "signature/manifest-v1.json",
        "migration/receipt-signer.pub",
    }
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


def test_every_kind_and_version_has_a_golden_file() -> None:
    """Every format version of every persisted kind that this release knows has a file in the
    corpus (the older ones from the release that wrote them), so dropping a reader is a failure."""
    from shape.compat import KINDS

    have = {(e["kind"], e["format_version"]) for _, e in ENTRIES}
    # a profile artifact and a model artifact are both kind "shape" files; the corpus names them
    # by what they hold
    want = {(k.name, v) for k in KINDS.values() for v in range(1, k.current + 1)}
    assert want <= have, f"no golden file for {sorted(want - have)}"


def test_every_corpus_kind_is_a_kind_of_the_policy() -> None:
    from shape.compat import KINDS

    assert {e["kind"] for _, e in ENTRIES} <= set(KINDS)


# What the writers of the first generation and of the unified-keys generation wrote reads to the
# same content: renaming the version keys changed no content.
DETERMINISTIC = [
    "artifact-v1",
    "artifact-v2",
    "artifact-v1-signed",
    "artifact-v2-signed",
    "model-v2",
    "model-engine-v1",
    "safe-profile-v1",
    "generation-schema-v1",
    "scenario-pack-v1",
    "generation-spec-v1",
    "gate-schema-v1",
    "verify-config-v1",
    "contract-v1",
    "contract-model-v1",
    "profile-artifact-v1",
    "profile-export-v1",
    "signature-v1",
]


@pytest.mark.parametrize("entry_id", DETERMINISTIC)
def test_old_and_new_writers_give_the_same_content(entry_id: str) -> None:
    old = (EXPECTED / "base" / f"{entry_id}.json").read_text(encoding="utf-8")
    new = (EXPECTED / "unified-keys" / f"{entry_id}.json").read_text(encoding="utf-8")
    assert old == new


def test_the_unified_generation_declares_the_unified_keys() -> None:
    """...and it really is the unified form (the old generation is the one without)."""
    import zipfile

    def manifest(generation: str, name: str) -> dict:
        with zipfile.ZipFile(CORPUS / generation / "artifact" / name) as z:
            return json.loads(z.read("manifest.json"))

    old, new = manifest("base", "model-v2.shape"), manifest("unified-keys", "model-v2.shape")
    assert "version" not in old and "shape_version" not in old
    assert new["version"] == 2 and new["format_version"] == 2 and "shape_version" in new
    safe_old = json.loads((CORPUS / "base" / "safe" / "orders.safe.json").read_text())
    safe_new = json.loads((CORPUS / "unified-keys" / "safe" / "orders.safe.json").read_text())
    assert "format" not in safe_old and safe_new["format"] == "shape-safe-profile"
