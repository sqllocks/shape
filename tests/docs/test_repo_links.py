"""Own-repository GitHub links must be checked locally before network exclusions."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check_repo_links.py"
PREFIX = "https://github.com/sqllocks/shape/"


def run_check(root: Path, site: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--site", str(site)],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("kind,path", [("blob", "a file.txt"), ("tree", "folder")])
def test_existing_repository_paths(tmp_path: Path, kind: str, path: str) -> None:
    site = tmp_path / "site"
    site.mkdir()
    target = tmp_path / path
    if kind == "tree":
        target.mkdir()
    else:
        target.touch()
    encoded = path.replace(" ", "%20")
    (site / "index.html").write_text(
        f'<a href="{PREFIX}{kind}/main/{encoded}?plain=1&amp;x=2#anchor">source</a>'
    )
    result = run_check(tmp_path, site)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("page", ["index.html", "404.html", "nested/page.html"])
def test_missing_path_fails_in_every_html_page(tmp_path: Path, page: str) -> None:
    site = tmp_path / "site"
    html = site / page
    html.parent.mkdir(parents=True)
    html.write_text(f'<img src="{PREFIX}blob/main/missing.png">')
    result = run_check(tmp_path, site)
    assert result.returncode == 1
    assert page in result.stdout
    assert "missing.png" in result.stdout


@pytest.mark.parametrize("target", ["../outside.txt", "%2Foutside.txt"])
def test_path_cannot_escape_checkout(tmp_path: Path, target: str) -> None:
    root = tmp_path / "repo"
    site = root / "site"
    site.mkdir(parents=True)
    (tmp_path / "outside.txt").touch()
    (site / "index.html").write_text(f'<a href="{PREFIX}blob/main/{target}">bad</a>')
    result = run_check(root, site)
    assert result.returncode == 1


def test_other_repositories_and_branches_are_not_local(tmp_path: Path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text(
        '<a href="https://github.com/other/repo/blob/main/missing">external</a>'
        f'<a href="{PREFIX}blob/release/0.9.3/missing">branch</a>'
        f'<a href="{PREFIX}issues/999">issue</a>'
    )
    assert run_check(tmp_path, site).returncode == 0


def test_missing_site_is_usage_error(tmp_path: Path) -> None:
    assert run_check(tmp_path, tmp_path / "missing").returncode == 2


def test_workflow_checks_repo_links_before_lychee_with_retries() -> None:
    workflow = (ROOT / ".github/workflows/docs-links.yml").read_text()
    assert "python scripts/check_repo_links.py --site site" in workflow
    assert workflow.index("check_repo_links.py") < workflow.index("lycheeverse/lychee-action")
    assert "--retry-wait-time 5" in workflow
    assert "--max-retries 3" in workflow
    assert "--exclude '^https://github\\.com/sqllocks/shape/(blob|tree)/main/'" in workflow
    assert "fail: true" in workflow
    assert "--accept" not in workflow
