"""#337 part 1: code the tests run only in a child process (the CLI tests, the chunk worker,
``shape`` git commands) is measured. coverage.py measures child Python processes when
``[tool.coverage.run] patch`` holds ``subprocess``, which it supports from 7.10."""

import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[2]
CONFIG = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_coverage_measures_subprocesses():
    assert "subprocess" in CONFIG["tool"]["coverage"]["run"]["patch"]


def test_dev_extra_requires_a_coverage_that_supports_the_subprocess_patch():
    reqs = [Requirement(r) for r in CONFIG["project"]["optional-dependencies"]["dev"]]
    (coverage,) = [r for r in reqs if r.name == "coverage"]
    assert not coverage.specifier.contains(Version("7.9.2"))
    assert coverage.specifier.contains(Version("7.10.0"))
