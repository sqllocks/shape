"""openpyxl's XML hardening needs defusedxml declared, not installed by chance (#301)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[2]


def test_excel_and_dev_declare_defusedxml():
    # openpyxl hardens its XML parsing only when defusedxml is importable (issue #301)
    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    extras = project["optional-dependencies"]
    for name in ("excel", "dev"):
        declared = {canonicalize_name(Requirement(d).name) for d in extras[name]}
        assert "defusedxml" in declared, f"[{name}] must declare defusedxml"


def test_defusedxml_floor_is_a_release_with_the_parsers_openpyxl_uses():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    req = next(
        Requirement(d)
        for d in project["optional-dependencies"]["excel"]
        if canonicalize_name(Requirement(d).name) == "defusedxml"
    )
    bounds = [Version(s.version) for s in req.specifier if s.operator in (">=", "==", "~=")]
    assert bounds and max(bounds) >= Version("0.7")
