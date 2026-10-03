"""P2-06 acceptance: an example plugin installed from outside the tree passes the kit, and every
first-party skeleton builds a wheel.

The example (``examples/plugin``) is built and installed into a scratch directory with pip, then
used from a working directory outside the repository: the kit, its own tests, ``shape plugins
list`` and ``shape hello`` all run in subprocesses against that installation (the G2 check: a
source, a detector and a command added with no change to core).
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from shape.builtins.catalog import BUILTINS

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "plugin"


# The skeletons' build-system needs setuptools>=77 (PEP 639 license strings); older ones fail.
MIN_SETUPTOOLS = 77


def _pip_args() -> list[str]:
    # Without network, build with the setuptools already installed when it is new enough for the
    # skeletons; otherwise pip's isolated build fetches the version the build-system asks for.
    if importlib.util.find_spec("setuptools") is None:
        return []
    major = importlib.metadata.version("setuptools").split(".")[0]
    return ["--no-build-isolation"] if major.isdigit() and int(major) >= MIN_SETUPTOOLS else []


@pytest.fixture(scope="module")
def outside(tmp_path_factory):
    """(site dir with the plugin installed, a working directory outside the repository)."""
    base = tmp_path_factory.mktemp("outside")
    site, work = base / "site", base / "work"
    work.mkdir()
    # Install from a copy so building leaves no build/ or egg-info in the repository.
    src = base / "plugin-src"
    shutil.copytree(
        EXAMPLE, src, ignore=shutil.ignore_patterns("build", "*.egg-info", "__pycache__")
    )
    done = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "--no-deps", "--target", str(site)]
        + _pip_args()
        + [str(src)],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return site, work


def run(outside, *args, cwd=None):
    site, work = outside
    env = dict(os.environ)
    env["PYTHONPATH"] = str(site)
    return subprocess.run(
        [sys.executable, *args], capture_output=True, text=True, env=env, cwd=cwd or work
    )


def shape(outside, *args):
    return run(
        outside, "-c", "import sys; from shape.cli.main import main; sys.exit(main())", *args
    )


def test_installed_example_passes_the_kit(outside):
    r = run(outside, "-m", "shape.plugins.kit", "shape-example-plugin")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "OK shape-example-plugin: 3 plugin(s) conform" in r.stdout
    for key in ("shape.sources:lines", "shape.detectors:iban", "shape.commands:hello"):
        assert f"{key}: ok" in r.stdout


def test_example_own_tests_pass_against_the_installation(outside):
    site, work = outside
    tests = work / "tests"
    shutil.copytree(EXAMPLE / "tests", tests)
    r = run(outside, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(tests), cwd=work)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "4 passed" in r.stdout


def test_g2_plugin_adds_source_detector_command_next_to_every_builtin(outside):
    r = shape(outside, "plugins", "list", "--json")
    assert r.returncode == 0, r.stderr
    rows = {(x["group"], x["name"]): x for x in json.loads(r.stdout)}
    for key in (
        ("shape.sources", "lines"),
        ("shape.detectors", "iban"),
        ("shape.commands", "hello"),
    ):
        assert rows[key]["source"] == "shape-example-plugin"
    builtin = {(g, n) for g, n, _ in BUILTINS}
    assert builtin <= set(rows) and len(builtin) == len(BUILTINS)
    assert all(rows[k]["source"] != "shape-example-plugin" for k in builtin)
    hello = shape(outside, "hello", "--name", "Ada")
    assert hello.returncode == 0 and hello.stdout.strip() == "hello, Ada"
    doctor = shape(outside, "plugins", "doctor")
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr


def test_kit_fails_a_distribution_that_is_not_installed(outside):
    r = run(outside, "-m", "shape.plugins.kit", "no-such-plugin-distribution")
    assert r.returncode == 1 and "not installed" in r.stderr


# -- first-party skeletons ------------------------------------------------------------------


def _script():
    spec = importlib.util.spec_from_file_location(
        "check_plugin_skeletons", ROOT / "scripts" / "check_plugin_skeletons.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_skeleton_tree_follows_the_lockstep_rules():
    script = _script()
    assert script.check_tree() == []
    assert set(script.EXPECTED) == {
        "kafka",
        "eventhubs",
        "fabric",
        "sqlserver",
        "domains",
        "simulation",
        "dbt",
    }


def test_skeleton_check_catches_version_drift(monkeypatch):
    script = _script()
    monkeypatch.setattr(script, "core_version", lambda: "9.9.9")
    problems = script.check_tree()
    assert len(problems) >= 7 and all("9.9.9" in p for p in problems if "version" in p)


def test_every_skeleton_builds_a_pure_wheel(tmp_path):
    script = _script()
    assert script.build_wheels(tmp_path, isolated=not _pip_args()) == []
    assert len(list(tmp_path.glob("*.whl"))) == 7
    assert not list((ROOT / "plugins").rglob("build")), "build left files in the source tree"
