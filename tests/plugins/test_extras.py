"""The core extras name every first-party plugin, pinned to the core version (T-08, T-09)."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORE = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
EXTRAS = CORE["optional-dependencies"]
VERSION = CORE["version"]
DISTS = sorted(
    f"sqllocks-shape-{d.name.removeprefix('shape-')}" for d in (ROOT / "plugins").iterdir()
)


def _pin(dist: str) -> str:
    return f"{dist}=={VERSION}"


def test_there_are_ten_first_party_distributions() -> None:
    assert len(DISTS) == 10


def test_dbt_and_healthcare_extras_are_pinned_to_the_core_version() -> None:
    assert EXTRAS["dbt"] == [_pin("sqllocks-shape-dbt")]
    assert EXTRAS["healthcare"] == [
        _pin("sqllocks-shape-behavior"),
        _pin("sqllocks-shape-healthcare-codes"),
        _pin("sqllocks-shape-healthcare-standards"),
    ]


def test_all_includes_every_plugin_distribution() -> None:
    assert {_pin(d) for d in DISTS} <= set(EXTRAS["all"])


def test_every_plugin_dependency_in_an_extra_is_pinned_to_the_core_version() -> None:
    for name, reqs in EXTRAS.items():
        for req in reqs:
            if req.startswith("sqllocks-shape-"):
                assert req == _pin(req.split("==")[0]), f"[{name}] {req}"
