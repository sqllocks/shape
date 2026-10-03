"""Declared floors must not admit versions with known advisories (#267)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[2]

# Lowest release of each package that carries the fix for a published advisory. A declared floor
# must not admit anything older (issue #267). pyarrow is deliberately absent: its floor is held
# at 14.0.1 so that Fabric runtimes and the Fabric UDF SDK install (T-07).
ADVISORY_FIXED_IN = {
    "azure-identity": Version("1.16.1"),  # PYSEC-2026-1209
    "pymysql": Version("1.1.1"),  # PYSEC-2026-502
    "scikit-learn": Version("1.5.0"),  # PYSEC-2024-110
    "pytest": Version("9.0.3"),  # PYSEC-2026-1845 (the [dev] extra)
}


def _pyprojects() -> list[Path]:
    return [ROOT / "pyproject.toml", *sorted((ROOT / "plugins").glob("*/pyproject.toml"))]


def _declared(path: Path) -> list[tuple[str, Requirement]]:
    """(where, requirement) for every dependency and optional dependency in one pyproject."""
    project = tomllib.loads(path.read_text("utf-8"))["project"]
    out = [("dependencies", Requirement(d)) for d in project.get("dependencies", [])]
    for extra, deps in project.get("optional-dependencies", {}).items():
        out += [(f"extra {extra}", Requirement(d)) for d in deps]
    return out


def _floor(req: Requirement) -> Version | None:
    """The lowest version the specifier admits, or None when it has no lower bound."""
    bounds = [Version(s.version) for s in req.specifier if s.operator in (">=", "==", "~=")]
    return max(bounds) if bounds else None


def test_floors_do_not_admit_versions_with_known_advisories():
    problems = []
    seen = set()
    for path in _pyprojects():
        for where, req in _declared(path):
            name = canonicalize_name(req.name)
            fixed = ADVISORY_FIXED_IN.get(name)
            if fixed is None:
                continue
            seen.add(name)
            floor = _floor(req)
            if floor is None or floor < fixed:
                problems.append(
                    f"{path.relative_to(ROOT)} ({where}): {req} admits versions below {fixed}"
                )
    assert not problems, "\n".join(problems)
    # the table must not rot: every package in it is still declared somewhere
    assert seen == set(ADVISORY_FIXED_IN)
