"""W1-15 (#92) deliverable 6: pinned fixtures in CI.

``pinned/<name>.json`` is a spec per built-in strategy and per distribution family, fully pinned
with ``shape pin``; ``pinned/expected.json`` holds the dataset id of seed 42 at 200 rows per table
for every set of generator versions that was released. This runs in the regular suite, in both
kernel modes. Changing what a strategy produces without raising its version changes the id and
fails ``test_the_dataset_id_of_every_pinned_spec_is_unchanged``; raising the version needs the new
id added (``python tests/generation/pinned_support.py --write``) and the old id must still match.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from typing import Any

import pinned_support as ps
import pytest
from versioning_fixtures import PROBE, TwoVersions, probes, spec_doc

from shape import schemacheck
from shape.generation import pinning, spec_keys
from shape.generation.spec_edit import SpecDocument
from shape.generation.spec_schema import published_schema, strategy_names
from shape.kernel import dispatch

EXPECTED = ps.load_expected()


@pytest.fixture(autouse=True)
def _datasets() -> Iterator[None]:
    with ps.datasets():
        yield


@pytest.fixture(params=["python", "rust"])
def kernel(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    if request.param == "rust":
        pytest.importorskip("shape._kernel")
    monkeypatch.setenv("SHAPE_KERNEL", request.param)
    dispatch.reset()
    assert dispatch.kernel_name() == request.param
    yield request.param
    dispatch.reset()


def entries() -> list[tuple[str, dict[str, Any]]]:
    return [(stem, e) for stem, found in EXPECTED["specs"].items() for e in found]


# ---- the corpus -----------------------------------------------------------------------------


def test_the_corpus_has_a_spec_per_strategy_and_per_distribution_family() -> None:
    from shape.builtins.distributions.families import FAMILIES

    families = {f"distribution-{name}" for name in spec_keys.FAMILY_KEYS if name in FAMILIES}
    assert set(ps.stems()) == set(strategy_names()) | families
    assert set(EXPECTED["specs"]) == set(ps.stems())


@pytest.mark.parametrize("stem", ps.stems())
def test_a_pinned_spec_is_valid_and_pins_everything_it_uses(stem: str) -> None:
    doc = ps.load_spec(stem)
    assert schemacheck.validate(doc, published_schema()) == []
    report = pinning.inspect(SpecDocument.from_dict(doc), stem)
    assert report.unpinned == [] and report.unused == [], f"{stem}: run `shape pin`"
    assert report.pinned == doc["generators"]


def test_a_spec_is_listed_under_the_strategy_it_is_named_for() -> None:
    for stem in strategy_names():
        used = {
            str(c["generator"].get("strategy"))
            for t in ps.load_spec(stem)["tables"].values()
            for c in t["columns"].values()
        }
        assert stem in used, stem


def test_expected_is_a_versioned_format() -> None:
    assert EXPECTED["format"] == ps.FORMAT == "generator-pinned-ids"
    assert EXPECTED["version"] == ps.VERSION == 1
    assert (EXPECTED["seed"], EXPECTED["rows"]) == (ps.SEED, ps.ROWS) == (42, 200)
    for stem, found in EXPECTED["specs"].items():
        assert found, stem
        keys = [json.dumps(e["generators"], sort_keys=True) for e in found]
        assert len(keys) == len(set(keys)), f"{stem}: a set of versions is listed twice"
        for e in found:
            assert e["id"].startswith("sha256:") and len(e["id"]) == 71


# ---- the promise ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stem,entry", entries(), ids=[f"{s}-{json.dumps(e['generators'])}" for s, e in entries()]
)
def test_the_dataset_id_of_every_pinned_spec_is_unchanged(
    kernel: str, stem: str, entry: dict[str, Any]
) -> None:
    got = ps.dataset_id_of(ps.load_spec(stem), entry["generators"])
    assert got == entry["id"], (
        f"{stem} at generator versions {entry['generators']} no longer gives {entry['id']}: "
        "a generator changed its output without a new generator_version"
    )


@pytest.mark.parametrize("stem", ps.stems())
def test_the_spec_as_committed_gives_the_expected_id(kernel: str, stem: str) -> None:
    doc = ps.load_spec(stem)
    ids = {json.dumps(e["generators"], sort_keys=True): e["id"] for e in EXPECTED["specs"][stem]}
    assert ps.dataset_id_of(doc) == ids[json.dumps(doc["generators"], sort_keys=True)]


@pytest.mark.parametrize("stem", ps.stems())
def test_every_current_version_has_an_expected_id(stem: str) -> None:
    """A version that was raised must come with its new id."""
    latest = ps.engine(ps.load_spec(stem), {}).generator_versions
    known = [e["generators"] for e in EXPECTED["specs"][stem]]
    assert latest in known, f"{stem}: add the id for {latest} (pinned_support.py --write)"


def test_the_kernels_agree_on_every_id() -> None:
    """The promise is for both kernels: the expected ids above are the same in each mode."""
    pytest.importorskip("shape._kernel")
    import os
    import subprocess
    import sys
    from pathlib import Path

    code = (
        f"import json, sys; sys.path.insert(0, {str(Path(ps.__file__).parent)!r})\n"
        "import pinned_support as ps\n"
        "with ps.datasets():\n"
        "    out = {s: ps.dataset_id_of(ps.load_spec(s)) for s in ps.stems()}\n"
        "print(json.dumps(out))\n"
    )
    found = {}
    for mode in ("python", "rust"):
        env = {**os.environ, "SHAPE_KERNEL": mode}
        run = subprocess.run(
            [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True
        )
        found[mode] = json.loads(run.stdout.strip().splitlines()[-1])
    assert found["python"] == found["rust"]


# ---- the test has teeth ---------------------------------------------------------------------


def test_changing_a_strategys_output_without_a_new_version_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from shape.builtins.strategies import Sequence

    stem, entry = "sequence", EXPECTED["specs"]["sequence"][0]
    assert ps.dataset_id_of(ps.load_spec(stem), entry["generators"]) == entry["id"]
    original = Sequence.generate

    def shifted(self: Any, spec: Any, ctx: Any) -> Any:
        import pyarrow.compute as pc

        return pc.add(original(self, spec, ctx), 1)

    monkeypatch.setattr(Sequence, "generate", shifted)
    assert ps.dataset_id_of(ps.load_spec(stem), entry["generators"]) != entry["id"]


def test_raising_a_version_and_adding_its_id_passes_while_the_old_id_still_matches() -> None:
    """Simulated with the two-version test strategy: both entries match, each at its version."""
    with probes():
        doc = spec_doc()
        spec = SpecDocument.from_dict(doc)
        pinning.pin(spec)  # pinned at the current version (2)
        pinned = spec.to_dict()
        v1 = ps.dataset_id_of(pinned, {PROBE: 1, "sequence": 1})
        v2 = ps.dataset_id_of(pinned, {PROBE: 2, "sequence": 1})
        assert v1 != v2
        known = [
            {"generators": {PROBE: 1, "sequence": 1}, "id": v1},
            {"generators": {PROBE: 2, "sequence": 1}, "id": v2},
        ]
        for e in known:
            assert ps.dataset_id_of(pinned, e["generators"]) == e["id"]
        # the spec as committed (pinned at 2) and an unpinned run both give the new id
        assert ps.dataset_id_of(pinned) == v2 == ps.dataset_id_of(doc)
        # a spec pinned at 1 keeps giving the old id
        old = copy.deepcopy(pinned)
        old["generators"][PROBE] = 1
        assert ps.dataset_id_of(old) == v1
        latest = ps.engine(pinned, {}).generator_versions
        assert latest in [e["generators"] for e in known]


def test_a_version_raised_without_its_id_is_found() -> None:
    """The check behind ``test_every_current_version_has_an_expected_id`` has something to find."""
    with probes():
        latest = ps.engine(spec_doc(), {}).generator_versions
        known = [{"generators": {PROBE: 1, "sequence": 1}}]
        assert latest not in [e["generators"] for e in known]
        assert TwoVersions.generator_version == 2
