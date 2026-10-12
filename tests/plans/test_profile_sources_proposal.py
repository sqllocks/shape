"""The PROFILE-SOURCES proposal names its work packages once each, and every one of them has
acceptance criteria, a dependency line and a row in the estimate table."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROPOSAL = ROOT / "docs/plans/proposals/PROFILE-SOURCES.md"

_HEADING = re.compile(r"^\*\*(PS-\d{2}) — .+\*\*$", re.M)
_ESTIMATE_ROW = re.compile(r"^\| (PS-\d{2}) \|", re.M)


def _bullet(body: str, name: str) -> str | None:
    """The text of the ``- name:`` bullet of a package, continuation lines joined."""
    match = re.search(rf"^- {name}: (.+(?:\n  [^-\n].*)*)", body, re.M)
    return " ".join(match.group(1).split()) if match else None


def _packages() -> dict[str, str]:
    """``{id: body}`` for every work-package heading, in document order."""
    text = PROPOSAL.read_text(encoding="utf-8")
    matches = list(_HEADING.finditer(text))
    assert matches, "the proposal declares no work package"
    out: dict[str, str] = {}
    for match, following in zip(matches, matches[1:] + [None], strict=True):
        end = following.start() if following else len(text)
        out.setdefault(match.group(1), text[match.end() : end])
    return out


def test_work_package_ids_are_unique() -> None:
    text = PROPOSAL.read_text(encoding="utf-8")
    ids = _HEADING.findall(text)
    assert len(ids) == len(set(ids)), f"duplicate work-package ids: {sorted(ids)}"
    assert ids == sorted(ids), "work packages are declared out of order"


def test_every_work_package_has_acceptance_depends_and_an_estimate() -> None:
    packages = _packages()
    estimated = set(_ESTIMATE_ROW.findall(PROPOSAL.read_text(encoding="utf-8")))
    for wp, body in packages.items():
        assert re.search(r"^- Acceptance:", body, re.M), f"{wp} has no acceptance line"
        assert re.search(r"^  - .+", body, re.M), f"{wp} lists no acceptance criterion"
        depends = _bullet(body, "Depends")
        assert depends is not None and depends.endswith("."), f"{wp} has no dependency line"
        assert re.search(r"^- Fixes: none\.$", body, re.M), f"{wp} has no fixes line"
        assert wp in estimated, f"{wp} has no row in the estimate table"


def test_dependencies_name_declared_work_packages() -> None:
    packages = _packages()
    for wp, body in packages.items():
        depends = _bullet(body, "Depends")
        assert depends is not None, wp
        for ref in re.findall(r"PS-\d{2}", depends):
            assert ref in packages, f"{wp} depends on undeclared {ref}"
            assert ref < wp, f"{wp} depends on a later package {ref}"
