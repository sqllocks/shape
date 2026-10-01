"""PF-05: the Dockerfile and its CI workflow say what the plan requires.

These are static checks. The image itself is built, size-checked and run (`shape profile` on D1
mounted from the host) by `.github/workflows/container.yml`; there is no Docker daemon in the
builder sessions, so the build is verified by CI, not here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github" / "workflows" / "container.yml").read_text(encoding="utf-8")


def _stages() -> list[str]:
    return re.split(r"(?m)^(?=FROM )", DOCKERFILE)[1:]


def test_runtime_image_is_python_311_slim():
    runtime = _stages()[-1]
    assert runtime.startswith("FROM python:3.11-slim\n")


def test_runtime_installs_the_wheel_with_the_azure_extra():
    runtime = _stages()[-1]
    assert re.search(r"pip install .*sqllocks_shape-\*\.whl\)\[azure\]", runtime)
    assert "--mount=type=bind,from=build,source=/dist" in runtime  # the wheel is not a layer
    assert "import adlfs, azure.identity, deltalake" in runtime  # a broken trim fails the build


def test_runtime_runs_as_a_non_root_user_and_ends_on_it():
    runtime = _stages()[-1]
    assert re.search(r"useradd .*--uid 10001", runtime)
    user_lines = [ln for ln in runtime.splitlines() if ln.startswith("USER ")]
    assert user_lines and user_lines[-1] == "USER shape"
    assert runtime.rstrip().splitlines()[-1].startswith("CMD ")  # nothing after USER runs as root


def test_the_build_stage_builds_the_rust_wheel_from_the_checkout():
    build = _stages()[0]
    assert "maturin build --release" in build
    for needed in ("pyproject.toml", "rust", "src", "LICENSE", "THIRD_PARTY_NOTICES.md"):
        assert re.search(rf"COPY .*\b{re.escape(needed)}\b", build), needed


def test_no_entrypoint_so_the_command_line_is_the_whole_command():
    code = [ln for ln in DOCKERFILE.splitlines() if not ln.lstrip().startswith("#")]
    assert not any(ln.startswith("ENTRYPOINT") for ln in code)


def test_dockerignore_keeps_the_context_small_and_the_sources_in():
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    assert {".git", "tests", "benchmarks", "rust/**/target"} <= set(ignore)
    for needed in ("src", "rust", "pyproject.toml", "README.md", "LICENSE"):
        assert needed not in ignore


def test_workflow_builds_checks_size_and_profiles_d1_in_the_image():
    assert "docker/build-push-action" in WORKFLOW
    assert "-lt 500000000" in WORKFLOW
    assert "datasets.py D1" in WORKFLOW
    assert re.search(r"-v \"\$BENCH_DATA_DIR/profile:/data:ro\"", WORKFLOW)
    assert "shape profile" in WORKFLOW


def test_workflow_publishes_to_ghcr_only_on_version_tags():
    assert re.search(r"if: startsWith\(github.ref, 'refs/tags/v'\)", WORKFLOW)
    assert "ghcr.io/sqllocks/shape" in WORKFLOW
    assert WORKFLOW.count("push: true") == 1
    publish = WORKFLOW[WORKFLOW.index("  publish:") :]
    assert "push: true" in publish and "packages: write" in publish
    assert "packages: write" not in WORKFLOW[: WORKFLOW.index("  publish:")]


@pytest.mark.parametrize("name", ["Dockerfile", ".dockerignore"])
def test_files_exist(name):
    assert (ROOT / name).is_file()
