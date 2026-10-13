"""``profile validate --safe`` on a dataset profile whose tables are wide enough to sample.

The sampling record of a table names the columns an analysis sampled. It once wrote them as a
list of names, which the leak scanner (rightly) reads as a list of raw strings; a multi-table
dataset of wide tables failed the scan. The record now refers to columns by position.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import shape
from shape.cli.main import main
from shape.privacy.cli import scanned_document
from shape.privacy.safe_validator import SafeProfileValidator


def _write_wide(path: Path, n: int, seed: int) -> None:
    rng = np.random.RandomState(seed)
    lines = ["id,a,b,c"]
    for i in range(n):
        lines.append(
            f"{i},{rng.normal(0, 1):.4f},{rng.normal(5, 2):.4f},{rng.exponential(1.0):.4f}"
        )
    path.write_text("\n".join(lines) + "\n")


@pytest.fixture
def prof(tmp_path: Path) -> Any:
    """A safe-capture dataset profile of three wide tables, written by the CLI as a user would."""
    src = tmp_path / "data"
    src.mkdir()
    for seed, name in enumerate(("first", "second", "third"), start=1):
        _write_wide(src / f"{name}.csv", 2400 + 100 * seed, seed)
    out = tmp_path / "ds.shape"
    assert main(["profile", str(src), "--dataset", "-o", str(out)]) == 0
    return shape.load(str(out))


def _scan(p: Any) -> Any:
    return SafeProfileValidator().validate_data(scanned_document(p))


def _as_written_by_091(prof: Any) -> dict[str, Any]:
    """The profile document with each ``column_indexes`` written back as a list of names."""
    doc = copy.deepcopy(scanned_document(prof))
    for table in doc["tables"].values():
        names = list(table["columns"])
        for entry in table["sampling"]["internal"]:
            if "column_indexes" in entry:
                entry["columns"] = [names[i] for i in entry.pop("column_indexes")]
    return doc


def test_a_clean_multi_table_profile_passes(prof: Any) -> None:
    prof = prof
    assert all(t["sampling"]["internal"] for t in prof.tables.values())  # the wide case is real
    assert _scan(prof).is_clean


def test_sampled_columns_are_positions_in_the_table(prof: Any) -> None:
    for table in prof.tables.values():
        names = list(table["columns"])
        for entry in table["sampling"]["internal"]:
            assert "columns" not in entry
            assert all(isinstance(i, int) and 0 <= i < len(names) for i in entry["column_indexes"])
        fit = next(e for e in table["sampling"]["internal"] if e["analysis"] == "distribution_fit")
        assert [names[i] for i in fit["column_indexes"]] == ["id", "a", "b", "c"]


def test_a_planted_leak_in_a_later_table_still_fails(prof: Any) -> None:
    doc = scanned_document(prof)
    doc = copy.deepcopy(doc)
    doc["tables"]["third"]["columns"]["a"]["sample_values"] = ["alice", "bob", "carol"]
    found = SafeProfileValidator().validate_data(doc).findings
    assert [f.rule for f in found] == ["raw-string-list"]
    assert found[0].path == "$.tables.third.columns.a.sample_values"


def test_values_planted_in_the_sampling_record_still_fail(prof: Any) -> None:
    doc = copy.deepcopy(scanned_document(prof))
    entry = doc["tables"]["second"]["sampling"]["internal"][0]
    entry["column_indexes"] = ["alice", "bob", "carol"]
    found = SafeProfileValidator().validate_data(doc).findings
    assert [f.rule for f in found] == ["raw-string-list"]
    assert found[0].path == "$.tables.second.sampling.internal[0].column_indexes"


def test_a_profile_written_by_091_still_reports_its_name_lists(prof: Any) -> None:
    found = SafeProfileValidator().validate_data(_as_written_by_091(prof)).findings
    assert found and {f.rule for f in found} == {"raw-string-list"}
    assert all(f.path.endswith(".columns") and ".sampling.internal[" in f.path for f in found)


def test_a_single_table_profile_is_unchanged(tmp_path: Path) -> None:
    csv = tmp_path / "t.csv"
    _write_wide(csv, 2500, 4)
    out = tmp_path / "t.shape"
    assert main(["profile", str(csv), "-o", str(out)]) == 0
    assert _scan(shape.load(str(out))).is_clean


def test_a_profile_written_by_091_still_loads_and_reports(prof: Any, tmp_path: Path) -> None:
    from shape.profile.reference.profile import Profile

    old = Profile(_as_written_by_091(prof))
    assert set(old.tables) == {"first", "second", "third"}
    assert old.sampling()["first"]["internal"][0]["columns"]
    path = tmp_path / "old.shape"
    shape.save(old, str(path))
    again = shape.load(str(path))
    assert again.tables["third"]["sampling"] == old.tables["third"]["sampling"]


def test_generation_is_the_same_from_either_form(prof: Any) -> None:
    from shape.profile.reference.profile import Profile

    new = prof
    old = Profile(_as_written_by_091(new))
    a = shape.generate(new, seed=7)
    b = shape.generate(old, seed=7)
    assert set(a.tables) == set(b.tables)
    for name in a.tables:
        assert a.tables[name].equals(b.tables[name])
