"""W3-08 (#232): display of the new entries, and profiles written before them (the W3-07 profile
and the one just before this package) load, display, diff, check and generate."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pytest

import shape
from shape.cli.main import main
from shape.generation.copula_mixed import FORMAT, PROFILE_FORMAT, block_from_profile
from shape.profile.joint.display import describe_joint

FIXTURES = Path(__file__).parents[1] / "fixtures" / "profiles"
NEW_KEYS = ("multivariate_outliers", "pca", "cohorts", "copula", "multi_determinant_capped")


def _table(n: int = 3000, seed: int = 3) -> pa.Table:
    rng = np.random.default_rng(seed)
    z = rng.multivariate_normal([0, 0, 0], [[1, 0.8, 0.3], [0.8, 1, 0.2], [0.3, 0.2, 1]], n)
    seg = np.array(["p", "q", "r"])[np.searchsorted(np.quantile(z[:, 1], [0.5, 0.8]), z[:, 1])]
    return pa.table(
        {
            "a": pa.array(z[:, 0]),
            "b": pa.array(z[:, 1]),
            "c": pa.array(z[:, 2]),
            "g": pa.array(seg),
            "store": pa.array([f"s{i % 7}" for i in range(n)]),
            "reg": pa.array([f"r{(i // 7) % 3}" for i in range(n)]),
            "cash": pa.array([f"c{(i % 7) * 3 + (i // 7) % 3}" for i in range(n)]),
        }
    )


def _joint(profile: Any) -> dict[str, Any]:
    return dict(profile.to_dict()["joint"])


def test_show_prints_the_new_entries(tmp_path: Path, capsys: Any) -> None:
    shape.save(shape.profile(_table()), str(tmp_path / "p.shape"))
    assert main(["show", str(tmp_path / "p.shape")]) == 0
    doc = json.loads(capsys.readouterr().out)
    joint = doc["profile"]["joint"]
    for key in ("multivariate_outliers", "pca", "cohorts", "copula"):
        assert key in joint
    assert any(len(d["determinant"]) == 2 for d in joint["dependencies"])


def test_profile_html_has_a_line_per_entry(tmp_path: Path) -> None:
    html = shape.profile(_table()).to_html()
    assert "Joint structure" in html
    for needle in (
        "(reg, store) -&gt; cash",
        "multivariate outliers",
        "effective dimensions",
        "cohorts",
        "copula",
    ):
        assert needle in html, needle
    out = tmp_path / "r.html"
    p = shape.profile(_table())
    shape.save(p, str(tmp_path / "p.shape"))
    out.write_text(p.to_html())
    assert out.stat().st_size > 0


def test_the_lines_say_what_the_entries_hold() -> None:
    lines = describe_joint(_joint(shape.profile(_table())))
    text = "\n".join(lines)
    assert "(reg, store) -> cash: confidence 1.000" in text
    assert "effective dimensions of 3 numeric columns" in text
    assert "copula: 3 numeric and 4 categorical columns" in text
    assert describe_joint(None) == [] and describe_joint({}) == []


def test_the_no_joint_switch_leaves_nothing_to_display() -> None:
    p = shape.profile(_table(), joint=False)
    assert "joint" not in p.to_dict()
    assert "Joint structure" not in p.to_html()


def test_the_command_line_switch(tmp_path: Path) -> None:
    csv = tmp_path / "t.csv"
    import pyarrow.csv as pcsv

    pcsv.write_csv(_table(), csv)
    for flag, present in (("--joint", True), ("--no-joint", False)):
        out = tmp_path / f"{present}.shape"
        assert main(["profile", str(csv), "-o", str(out), flag]) == 0
        doc = shape.load(str(out)).to_dict()
        assert ("joint" in doc) is present
        if present:
            assert "pca" in doc["joint"]


def test_the_save_and_load_round_trip_keeps_every_entry(tmp_path: Path) -> None:
    p = shape.profile(_table())
    shape.save(p, str(tmp_path / "p.shape"))
    again = shape.load(str(tmp_path / "p.shape"))
    assert _joint(again) == _joint(p)
    json.dumps(_joint(p), allow_nan=False)  # JSON-safe


def test_persisted_blocks_declare_a_format_and_an_integer_version() -> None:
    cop = _joint(shape.profile(_table()))["copula"]
    assert cop["format"] == PROFILE_FORMAT and type(cop["version"]) is int
    block = block_from_profile({"table": {"copula": cop}})
    assert block is not None and block["format"] == FORMAT and type(block["version"]) is int
    assert block_from_profile({"table": {}}) is None and block_from_profile({"t": None}) is None


def test_a_version_one_copula_entry_is_read_as_it_was_written() -> None:
    """The entry as version 1 wrote it: a later reader must keep reading it."""
    entry = {
        "format": "shape.copula",
        "version": 1,
        "rows": 1000,
        "columns": ["x", "g"],
        "numeric": ["x"],
        "categorical": ["g"],
        "categories": {"g": ["a", "b"]},
        "correlation": [[1.0, 0.5], [0.5, 1.0]],
    }
    block = block_from_profile({"t": {"copula": entry}})
    assert block is not None
    assert block["tables"]["t"] == {
        "columns": ["x", "g"],
        "numeric": ["x"],
        "categories": {"g": ["a", "b"]},
        "correlation": [[1.0, 0.5], [0.5, 1.0]],
    }


# --- profiles written before this package ------------------------------------------------------


@pytest.mark.parametrize("name", ["pre_w3_07.shape", "pre_w3_08.shape"])
def test_an_older_profile_loads_displays_diffs_checks_and_generates(
    name: str, tmp_path: Path, capsys: Any
) -> None:
    old = shape.load(str(FIXTURES / name))
    doc = old.to_dict()
    tables = doc["tables"] if "tables" in doc else {"t": doc}
    for table in tables.values():
        for key in NEW_KEYS:
            assert key not in table.get("joint", {})
    # display
    assert main(["show", str(FIXTURES / name)]) == 0
    assert json.loads(capsys.readouterr().out)["kind"] == "profile"
    assert "<html" in old.to_html()
    assert (
        describe_joint(next(iter(tables.values())).get("joint")) == [] or name == "pre_w3_08.shape"
    )
    # diff against itself and against a new profile: no new kind
    assert not shape.diff(old, old).drifted
    new = shape.profile(_table())
    kinds = {c["kind"] for c in shape.diff(old, new).changes} | {
        c["kind"] for c in shape.diff(new, old).changes
    }
    assert not kinds & {"multivariate_outlier_rate_change", "structure_change", "cohort_shift"}
    # generate, with and without the opt-in
    for flag in (False, True):
        result = shape.generate(old, seed=1, mixed_copula=flag)
        assert all(t.num_rows > 0 for t in result.tables.values())
    # a contract rule on a pair of columns the profile has no pair for is "not measured"
    cols = list(next(iter(tables.values()))["columns"])[:3]
    check = shape.check(
        old,
        {"fd": [{"determinant": cols[:2], "dependent": cols[2], "min_confidence": 0.9}]},
    )
    assert not check.passed and "not measured" in str(check.violations[0]["observed"])


def test_the_plan_of_an_older_profile_does_not_mention_the_copula() -> None:
    from shape.generation.fit import fit_schema

    old = shape.load(str(FIXTURES / "pre_w3_08.shape"))
    assert not [i for i in fit_schema(old).plan.items if "copula" in i.evidence]
