"""Check owner-note visibility and release publication guards."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from check_docs_owners import inventory  # noqa: E402


def test_hidden_owner_inventory(tmp_path: Path) -> None:
    """Inventory rendered inputs and exclude private planning material."""
    (tmp_path / "docs/plans").mkdir(parents=True)
    (tmp_path / "docs/page.md").write_text("<!-- owner: confirm source -->")
    (tmp_path / "docs/plans/private.md").write_text("<!-- owner: private -->")
    assert inventory(tmp_path) == [("docs/page.md", 1, "confirm source")]


def test_no_visible_owner_placeholders() -> None:
    """Owner questions remain comments, never visible bracketed placeholders."""
    for path in (ROOT / "docs").rglob("*.md"):
        if {"plans", "talks"}.intersection(path.parts):
            continue
        assert not re.search(r"\[Owner:", path.read_text(), re.I), path


def test_release_guard_and_workflow() -> None:
    """Both tag gates fail while this checkout has unresolved owner notes."""
    run = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_docs_owners.py"), "--release"],
        text=True,
        capture_output=True,
    )
    assert run.returncode == int(bool(inventory()))
    workflow = yaml.safe_load((ROOT / ".github/workflows/docs.yml").read_text())
    for job in ("verify", "publish"):
        guards = [
            s
            for s in workflow["jobs"][job]["steps"]
            if s.get("run") == "python scripts/check_docs_owners.py --release"
        ]
        assert len(guards) == 1
        assert guards[0]["if"] == "github.ref_type == 'tag'"
