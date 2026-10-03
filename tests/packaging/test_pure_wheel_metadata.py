"""The pure py3-none-any wheel carries the same metadata as the platform wheels and the sdist.

T-29 ships both forms under one version, and installers assume every distribution of a version
has the same dependency metadata (issue #252). The reference here is what maturin writes for the
sdist (``PKG-INFO``), which is generated from ``pyproject.toml`` exactly as the platform wheels'
``METADATA`` is (checked by hand against a local ``maturin build``; see the lane status).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from email.parser import Parser
from pathlib import Path

import pytest
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_pure_wheel.py"
# header fields that must be identical in both distributions
SAME_FIELDS = (
    "Name",
    "Version",
    "Summary",
    "Requires-Python",
    "License-Expression",
    "Description-Content-Type",
)
MULTI_FIELDS = ("License-File", "Project-URL", "Classifier", "Provides-Extra")


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_pure_wheel", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_pure_wheel"] = module
    spec.loader.exec_module(module)
    return module


def _parse(text: str):
    return Parser().parsestr(text)


def _requires(msg) -> set[str]:
    """Requires-Dist lines in canonical form (spacing of markers differs between writers)."""
    return {str(Requirement(r)) for r in msg.get_all("Requires-Dist", [])}


@pytest.fixture(scope="module")
def pure(tmp_path_factory):
    wheel = _load_builder().build(tmp_path_factory.mktemp("pure"))
    with zipfile.ZipFile(wheel) as z:
        dist_info = next(n for n in z.namelist() if n.endswith(".dist-info/METADATA"))
        meta = _parse(z.read(dist_info).decode("utf-8"))
        entry_points = z.read(dist_info.replace("METADATA", "entry_points.txt")).decode()
    return meta, entry_points


@pytest.fixture(scope="module")
def sdist(tmp_path_factory):
    out = tmp_path_factory.mktemp("sdist")
    subprocess.run(
        [sys.executable, "-m", "maturin", "sdist", "--out", str(out)],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    archive = next(out.glob("*.tar.gz"))
    with tarfile.open(archive) as tar:
        member = next(
            m for m in tar.getmembers() if m.name.count("/") == 1 and m.name.endswith("/PKG-INFO")
        )
        extracted = tar.extractfile(member)
        assert extracted is not None
        return _parse(extracted.read().decode("utf-8"))


def test_requires_dist_equals_the_sdist(pure, sdist):
    pure_meta, _ = pure
    assert _requires(pure_meta) == _requires(sdist)


def test_every_extra_is_provided(pure, sdist):
    pure_meta, _ = pure
    declared = set(
        tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"][
            "optional-dependencies"
        ]
    )
    assert set(pure_meta.get_all("Provides-Extra", [])) == declared
    assert set(pure_meta.get_all("Provides-Extra", [])) == set(sdist.get_all("Provides-Extra", []))


@pytest.mark.parametrize("field", SAME_FIELDS)
def test_single_valued_fields_equal_the_sdist(pure, sdist, field):
    pure_meta, _ = pure
    assert pure_meta[field] == sdist[field]


@pytest.mark.parametrize("field", MULTI_FIELDS)
def test_multi_valued_fields_equal_the_sdist(pure, sdist, field):
    pure_meta, _ = pure
    assert sorted(pure_meta.get_all(field, [])) == sorted(sdist.get_all(field, []))


def test_description_equals_the_sdist(pure, sdist):
    pure_meta, _ = pure
    assert pure_meta.get_payload() == sdist.get_payload()


def test_entry_points_are_every_pyproject_entry_point(pure):
    import configparser

    _, text = pure
    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    expected = {"console_scripts": dict(project["scripts"])}
    expected.update({group: dict(eps) for group, eps in project["entry-points"].items()})
    parser = configparser.ConfigParser(delimiters=("=",))
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read_string(text)
    actual = {section: dict(parser[section]) for section in parser.sections()}
    assert actual == expected
