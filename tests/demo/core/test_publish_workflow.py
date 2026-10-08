"""Structural checks of .github/workflows/publish.yml (plan DM-03b).

The file name and the environment names must match what the owner registers as the PyPI
trusted publisher (O-01), and the workflow must hold no tokens or passwords. The checks are
plain-text so they run in an environment with nothing but numpy, pyarrow, pandas and deltalake.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "publish.yml"


def text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def code_lines() -> str:
    """The workflow without comment lines."""
    return "\n".join(ln for ln in text().splitlines() if not ln.lstrip().startswith("#"))


def test_file_name_is_what_the_owner_registers():
    assert WORKFLOW.name == "publish.yml"
    assert WORKFLOW.is_file()


def test_triggers_are_dispatch_with_repository_input_and_v_tags():
    body = code_lines()
    assert re.search(r"^on:\n  workflow_dispatch:\n    inputs:\n      repository:", body, re.M)
    assert re.search(r"options:\n\s+- testpypi\n\s+- pypi", body)
    assert re.search(r"push:\n    tags:\n      - \"v\*\"", body)


def test_environments_and_permissions():
    body = code_lines()
    assert "'testpypi'" in body and "'pypi'" in body
    assert re.search(r"environment: .*testpypi.*pypi", body)
    publish = body.split("  publish:", 1)[1]
    assert re.search(r"permissions:\n      id-token: write\n      contents: read", publish)
    # everything outside the publish job may only read the repository
    build = body.split("  publish:", 1)[0]
    assert "id-token" not in build
    assert re.search(r"^permissions:\n  contents: read", build, re.M)


def test_uses_trusted_publishing_and_no_secrets():
    body = code_lines()
    assert body.count("pypa/gh-action-pypi-publish@release/v1") == 2
    assert "https://test.pypi.org/legacy/" in body
    lowered = body.lower()
    assert "secrets." not in lowered
    for word in ("password", "api-token", "api_token", "twine upload"):
        assert word not in lowered
    assert not re.search(r"pypi-[A-Za-z0-9_-]{40,}", body)  # no literal API token


def test_builds_wheel_and_sdist_checks_them_and_runs_the_core_tests_on_311():
    body = code_lines()
    assert 'python-version: "3.11"' in body
    assert "scripts/build_pure_wheel.py --verify" in body  # builds and runs tests/demo/core
    assert "python -m build --sdist" in body
    assert "twine check dist/*" in body
    assert "early access" in body.lower()


def test_tag_must_match_the_package_version_and_version_is_not_a_prerelease():
    assert "GITHUB_REF_NAME" in code_lines()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), f"pre-release version {version}"
    import shape

    assert shape.__version__ == version
