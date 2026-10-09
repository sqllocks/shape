"""Regression checks for documentation navigation, issue links and publication safety."""

import hashlib
import importlib.util
import json
import re
import subprocess
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_diagrams_use_the_pinned_local_bundle() -> None:
    assets = ROOT / "docs/assets/javascripts"
    metadata = json.loads((assets / "mermaid-provenance.json").read_text())
    assert (
        hashlib.sha256((assets / "mermaid.min.js").read_bytes()).hexdigest() == metadata["sha256"]
    )
    config = (ROOT / "mkdocs.yml").read_text()
    assert "class: shape-mermaid" in config
    assert "assets/javascripts/diagrams.js" in config
    renderer = (assets / "diagrams.js").read_text()
    assert 'attributeFilter: ["data-md-color-scheme"]' in renderer


def test_series_is_coming_without_urls() -> None:
    topics = yaml.safe_load((ROOT / "docs/series.yml").read_text())
    for topic in topics:
        assert (ROOT / "docs" / topic["related_page"]).exists()
        if topic["status"] == "coming":
            assert not topic.get("post_url") and not topic.get("video_url")


def test_problem_form_accepts_page_url() -> None:
    form = yaml.safe_load((ROOT / ".github/ISSUE_TEMPLATE/docs-problem.yml").read_text())
    assert any(field.get("id") == "page-url" for field in form["body"])
    hooks = (ROOT / "scripts/mkdocs_hooks.py").read_text()
    assert '"page-url": page_url' in hooks
    config = (ROOT / "mkdocs.yml").read_text()
    assert "edit_uri" not in config
    assert "https://docs.shapedata.ai/" in config
    assert (ROOT / "docs/CNAME").read_text().strip() == "docs.shapedata.ai"


def test_committed_profile_and_notices() -> None:
    import shape

    p = shape.load(ROOT / "docs/assets/orders.shape")
    assert p.tables["orders"]["row_count"] == 40
    assert p.to_dict() == json.loads((ROOT / "docs/assets/orders-profile.json").read_text())
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text()
    assert "## Per-domain reference-data attribution" in notices
    assert notices.count("- Attribution:") == 6


def test_no_internal_links_on_public_pages() -> None:
    for p in (ROOT / "docs").rglob("*.md"):
        if {"plans", "talks"}.intersection(p.parts):
            continue
        assert not re.search(r"\]\([^)]*(?:plans|talks)/", p.read_text()), p


def test_dco_requires_a_trailer() -> None:
    spec = importlib.util.spec_from_file_location("dco", ROOT / "scripts/check_dco.py")
    dco = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dco)
    with tempfile.TemporaryDirectory() as tmp:

        def git(*args: str) -> str:
            return subprocess.check_output(["git", "-C", tmp, *args], text=True).strip()

        git("init", "-q")
        git("config", "user.name", "Test User")
        git("config", "user.email", "test@example.invalid")
        git("-c", "core.hooksPath=/dev/null", "commit", "--allow-empty", "-q", "-s", "-m", "base")
        base = git("rev-parse", "HEAD")
        git("-c", "core.hooksPath=/dev/null", "commit", "--allow-empty", "-q", "-m", "unsigned")
        bad = git("rev-parse", "HEAD")
        git("-c", "core.hooksPath=/dev/null", "commit", "--allow-empty", "-q", "-s", "-m", "signed")
        import os

        before = os.getcwd()
        try:
            os.chdir(tmp)
            assert dco.check(base, "HEAD") == [bad]
        finally:
            os.chdir(before)
