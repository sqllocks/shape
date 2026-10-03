"""W5-05: the nightly command runs every scenario of the library index, and the files it runs
are valid suites. The command itself is written in the lane status for the workflow."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from shape.scenario.library import formats
from shape.scenario.library.suite import list_suites, load_suite

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_library_suite.py"


def index_ids() -> list[str]:
    index = json.loads((formats.ROOT / "index.json").read_text(encoding="utf-8"))
    return [entry["id"] for entry in index["scenarios"]]


def built_suite(tmp_path: Path) -> Path:
    target = tmp_path / "library.json"
    subprocess.run([sys.executable, str(SCRIPT), str(target)], check=True)
    return target


def test_the_nightly_suite_file_covers_every_index_entry(tmp_path):
    suite = load_suite(built_suite(tmp_path))
    assert suite["scenarios"] == index_ids()
    assert len(index_ids()) >= 8


def test_every_index_entry_is_covered_by_the_nightly_command_or_a_built_in_suite(tmp_path):
    covered = set(load_suite(built_suite(tmp_path))["scenarios"])
    for name in list_suites():
        covered |= set(load_suite(name)["scenarios"])
    assert set(index_ids()) <= covered


def test_the_nightly_command_would_notice_a_scenario_added_to_the_index(tmp_path):
    spec = importlib.util.spec_from_file_location("build_library_suite", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    index = json.loads((formats.ROOT / "index.json").read_text(encoding="utf-8"))
    index["scenarios"].append({"id": "brand_new", "domain": "retail", "description": "x"})
    extra = tmp_path / "index.json"
    extra.write_text(json.dumps(index), encoding="utf-8")
    assert module.build_suite(extra)["scenarios"][-1] == "brand_new"


def test_the_script_needs_exactly_one_argument():
    done = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
    assert done.returncode == 2
    assert "usage" in done.stderr


@pytest.mark.parametrize("name", ["smoke", "schema-evolution"])
def test_the_built_in_suites_are_subsets_of_the_index(name):
    assert set(load_suite(name)["scenarios"]) <= set(index_ids())
