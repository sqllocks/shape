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


RELEASE = ROOT / ".github" / "workflows" / "release.yml"


def release_lines() -> str:
    """release.yml (P8-04: the build that publish.yml calls) without comment lines."""
    body = RELEASE.read_text(encoding="utf-8")
    return "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))


def jobs_with_id_token(body: str) -> set[str]:
    """The top-level jobs whose block grants ``id-token: write``."""
    jobs = re.split(r"^  (?=[a-z][a-z0-9-]*:$)", body.split("\njobs:\n", 1)[1], flags=re.M)
    return {j.split(":", 1)[0] for j in jobs if j.strip() and "id-token: write" in j}


def test_environments_and_permissions():
    body = code_lines()
    assert "'testpypi'" in body and "'pypi'" in body
    assert re.search(r"environment: .*testpypi.*pypi", body)
    publish = body.split("\n  publish:", 1)[1].split("\n  index-hashes:", 1)[0]
    assert re.search(r"permissions:\n      id-token: write\n      contents: read", publish)
    # The workflows themselves only read the repository. The OIDC token is granted to the upload
    # (publish), and to the build only for its Sigstore attestations (T-25): the reusable build's
    # sbom job is the one job there that holds it. Nothing else, the #266 guard included.
    assert re.search(r"^permissions:\n  contents: read", body, re.M)
    assert re.search(r"^permissions: \{contents: read\}$", release_lines(), re.M)
    assert jobs_with_id_token(body) == {"build", "publish"}
    assert jobs_with_id_token(release_lines()) == {"sbom"}
    assert "attestations: write" in release_lines().split("\n  sbom:", 1)[1]


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
    # P8-04: publish.yml uploads what release.yml built; release.yml builds every archive on
    # Python 3.11, verifies the pure wheel (installs it and runs tests/demo/core), checks the full
    # set and runs twine check --strict; check_versions.py keeps the early-access README check.
    assert "uses: ./.github/workflows/release.yml" in code_lines()
    body = release_lines()
    assert "python-version: '3.11'" in body
    assert "scripts/build_release_dist.py --out dist --verify-pure --python python3.11" in body
    assert "scripts/build_release_dist.py --check-set dist" in body
    assert "twine check --strict dist/*" in body
    assert "python scripts/check_versions.py" in body
    checker = (ROOT / "scripts" / "check_versions.py").read_text(encoding="utf-8")
    assert '"early access" not in readme.read_text(encoding="utf-8").lower()' in checker


def test_tag_must_match_the_package_version_and_version_is_not_a_prerelease():
    # release.yml checks a v* tag against every version (check_versions.py --tag), and the #266
    # guard lets PyPI publish only from a v* tag, so every PyPI upload has had its tag checked.
    assert '--tag "$GITHUB_REF_NAME"' in release_lines()
    assert "refs/tags/v*)" in code_lines()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), f"pre-release version {version}"
    import shape

    assert shape.__version__ == version
