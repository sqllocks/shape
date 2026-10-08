"""W3-07 (#103): the univariate depth fields inside ``shape.profile``: both kernels, the existing
fields and the share-safe profile untouched, older profiles, display, and bounded cost."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pytest

import shape
from shape.cli.main import main
from shape.profile import univariate as U
from shape.profile.reference import column as column_module

FIELDS = (
    "distribution_candidates",
    "distribution_by_bic",
    "zero_share",
    "zero_inflation",
    "heaping",
    "benford",
    "tail_index",
)
OLD = Path(__file__).parents[1] / "fixtures" / "profiles" / "pre_w3_07.shape"


def _table(n: int = 2000, seed: int = 3) -> pa.Table:
    rng = np.random.default_rng(seed)
    return pa.table(
        {
            "visits": pa.array(
                np.where(rng.random(n) < 0.3, 0, rng.poisson(3.0, n)).astype(np.int64)
            ),
            "amount": pa.array(np.round(10 ** rng.uniform(0.0, 4.0, n), 2)),
            "age": pa.array(rng.integers(18, 91, n).astype(np.int64)),
            "ratio": pa.array(rng.gamma(4.0, 2.0, n)),
            "city": pa.array([f"c{i}" for i in rng.integers(0, 12, n)]),
            "flag": pa.array(rng.random(n) < 0.4),
            "when": pa.array(np.arange(n).astype("datetime64[s]")),
        }
    )


def _columns(profile: Any) -> dict[str, dict[str, Any]]:
    return dict(profile.to_dict()["columns"])


# --- both kernels give the same fields -----------------------------------------------------------


def _profile_in(kernel: str) -> dict[str, Any]:
    code = (
        "import json, sys\n"
        "import numpy as np, pyarrow as pa, shape\n"
        "from tests.profile.test_univariate_profile import _table\n"
        "d = shape.profile(_table(), univariate=True).to_dict()\n"
        "print(json.dumps(d['columns'], sort_keys=True, allow_nan=False))\n"
    )
    root = Path(__file__).parents[2]
    env = {**os.environ, "SHAPE_KERNEL": kernel, "PYTHONPATH": str(root)}
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=root, check=False
    )
    assert done.returncode == 0, done.stderr
    return dict(json.loads(done.stdout.splitlines()[-1]))


def test_the_rust_and_python_kernels_give_identical_columns() -> None:
    pytest.importorskip("shape._kernel")
    py, rust = _profile_in("python"), _profile_in("rust")
    assert py == rust
    assert set(FIELDS) <= set(py["visits"])  # the comparison covered every new field


# --- existing output is not changed --------------------------------------------------------------


def test_the_existing_fields_are_byte_identical_with_the_new_ones_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table = _table()
    new = _columns(shape.profile(table, univariate=True))
    monkeypatch.setattr(column_module, "univariate_stats", lambda *a, **k: {})
    old = _columns(shape.profile(table, univariate=True))
    stripped = {c: {k: v for k, v in d.items() if k not in FIELDS} for c, d in new.items()}
    assert json.dumps(stripped, sort_keys=False) == json.dumps(old, sort_keys=False)
    # a continuous float column: model selection, zeros, Benford (not applicable here) and tail
    assert set(new["ratio"]) - set(old["ratio"]) == {
        "distribution_candidates",
        "distribution_by_bic",
        "zero_share",
        "benford",
        "tail_index",
    }


def test_only_numeric_columns_with_enough_values_gain_fields() -> None:
    cols = _columns(shape.profile(_table(), univariate=True))
    for name in ("city", "flag", "when"):
        assert not set(FIELDS) & set(cols[name]), name
    assert set(FIELDS) <= set(cols["visits"])  # a count column: the whole set
    assert "zero_inflation" not in cols["amount"] and "zero_share" in cols["amount"]
    nine = _columns(
        shape.profile(
            pa.table({"x": pa.array([float(i) + 0.5 for i in range(19)])}), univariate=True
        )
    )
    assert not set(FIELDS) & set(nine["x"])


def test_the_new_fields_are_json_safe_and_survive_save_and_load(tmp_path: Path) -> None:
    prof = shape.profile(_table(), univariate=True)
    path = tmp_path / "p.shape"
    shape.save(prof, path, capture="full")
    again = shape.load(path)
    assert again == prof
    json.dumps(again.to_dict(), allow_nan=False)
    from shape.artifact import read_artifact

    manifest, _ = read_artifact(str(path))
    # additive fields: no version of their own (the artifact is version 2 since W1-11's capture)
    assert manifest["format"] == "shape" and manifest["format_version"] == 2


# --- the share-safe profile is unchanged ---------------------------------------------------------


def test_the_share_safe_profile_does_not_carry_the_new_fields() -> None:
    from shape.privacy.safe_profile import to_safe_profile

    table = _table()
    new = to_safe_profile(shape.profile(table, univariate=True)).to_dict()
    text = json.dumps(new)
    for field in FIELDS:
        assert f'"{field}"' not in text, field
    old = shape.profile(table, univariate=True)
    doc = old.to_dict()
    for col in doc["columns"].values():
        for field in FIELDS:
            col.pop(field, None)
    stripped = to_safe_profile(type(old)(doc, name=old.name)).to_dict()
    assert json.dumps(new, sort_keys=True) == json.dumps(stripped, sort_keys=True)


# --- a profile written before this change --------------------------------------------------------


def test_the_frozen_older_profile_has_none_of_the_fields() -> None:
    cols = _columns(shape.load(OLD))
    assert not any(set(FIELDS) & set(c) for c in cols.values())


def test_an_older_profile_loads_displays_and_diffs() -> None:
    old = shape.load(OLD)
    assert old.summary()["columns"]["visits"]["dtype"] == "integer"
    assert "visits" in old.to_html()
    assert shape.diff(old, old).changes == []
    new = shape.profile(_table(300, 103), name="visits", univariate=True)
    d = shape.diff(old, new)
    assert not {
        c["kind"]
        for c in d.changes
        if c["kind"] in ("zero_inflation_change", "heaping_change", "benford_change", "tail_change")
    }
    d2 = shape.diff(new, old)
    assert not {c["kind"] for c in d2.changes} & {
        "zero_inflation_change",
        "heaping_change",
        "benford_change",
        "tail_change",
    }
    assert main(["show", str(OLD)]) == 0
    assert main(["show", str(OLD), "--pretty"]) == 0


# --- display -------------------------------------------------------------------------------------


def test_html_and_show_carry_the_new_fields(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    prof = shape.profile(_table(), univariate=True)
    s = prof.summary()["columns"]  # the summary's key set is a pinned contract: unchanged
    assert not set(FIELDS) & set(s["ratio"])
    html = prof.to_html()
    assert "best by BIC: gamma" in html
    assert "zero-inflated" in html and "Benford: " in html
    path = tmp_path / "t.shape"
    shape.save(prof, path, capture="full")
    capsys.readouterr()
    assert main(["show", str(path)]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["profile"]["columns"]["visits"]["zero_inflation"]["inflated"] is True
    assert shown["profile"]["columns"]["ratio"]["distribution_by_bic"] == "gamma"
    assert main(["show", str(path), "--pretty"]) == 0
    assert '"heaping"' in capsys.readouterr().out


def test_describe_lines() -> None:
    cols = _columns(shape.profile(_table(), univariate=True))
    lines = U.describe(cols["visits"])
    assert any(line.startswith("zero share ") and "inflated" in line for line in lines)
    assert U.describe(cols["city"]) == []
    flat = U.describe(
        {
            "zero_share": 0.1,
            "zero_inflation": {
                "inflated": False,
                "observed": 0.1,
                "poisson_expected": 0.1,
                "nb_expected": 0.1,
            },
            "benford": {"applicable": False, "reason": "fewer than 100 values"},
        }
    )
    assert flat == ["zero share 10.0%", "Benford: not applicable (fewer than 100 values)"]


# --- bounded cost --------------------------------------------------------------------------------


@pytest.mark.heavy
def test_a_ten_million_row_column_is_bounded_in_time_and_memory() -> None:
    n = 10_000_000
    rng = np.random.default_rng(5)
    floats = rng.lognormal(2.0, 1.5, n)
    counts = np.where(rng.random(n) < 0.25, 0, rng.poisson(4.0, n)).astype(np.float64)
    for values, integer in ((floats, False), (counts, True)):
        tracemalloc.start()
        t0 = time.perf_counter()
        out = U.univariate_stats(values, integer=integer)
        elapsed = time.perf_counter() - t0
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        assert "benford" in out and "distribution_by_bic" in out
        assert elapsed < 10.0, elapsed
        # temporaries of one chunk and the samples, never a copy of the column (80 MB)
        assert peak < 0.2 * values.nbytes, peak
    assert out["zero_inflation"]["inflated"] is True


def test_the_sampled_statistics_read_at_most_the_documented_sample() -> None:
    seen: list[int] = []
    real = U.heaping

    def spy(s: np.ndarray) -> Any:
        seen.append(len(s))
        return real(s)

    x = np.random.default_rng(6).lognormal(2.0, 1.0, 400_000)
    U.heaping = spy  # type: ignore[assignment]
    try:
        U.univariate_stats(x, integer=False)
    finally:
        U.heaping = real  # type: ignore[assignment]
    assert seen == [U.SAMPLE_CAP]
    assert len(U.sample_values(x, U.MODEL_CAP)) == U.MODEL_CAP


# --- opt-in (INT-18: the T-19 benchmark gate) -----------------------------------------------------


def test_without_univariate_the_profile_has_none_of_the_fields_and_is_otherwise_the_same() -> None:
    table = _table()
    plain = _columns(shape.profile(table))
    assert not any(set(U.FIELDS) & set(c) for c in plain.values())
    deep = _columns(shape.profile(table, univariate=True))
    assert set(FIELDS) <= set(deep["visits"])
    # U.FIELDS: also W7-03's mixture and seasonality, opt-in with the rest (INT-20)
    stripped = {c: {k: v for k, v in d.items() if k not in U.FIELDS} for c, d in deep.items()}
    assert json.dumps(stripped, sort_keys=False) == json.dumps(plain, sort_keys=False)


def test_univariate_reaches_a_dataset_and_a_workbook(tmp_path: Path) -> None:
    both = shape.profile({"a": _table(), "b": _table(200, 7)}, univariate=True).to_dict()
    assert all(set(FIELDS) <= set(t["columns"]["visits"]) for t in both["tables"].values())
    plain = shape.profile({"a": _table(), "b": _table(200, 7)}).to_dict()
    assert not any(
        set(FIELDS) & set(c) for t in plain["tables"].values() for c in t["columns"].values()
    )
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "counts"
    sheet.append(["visits"])
    for v in _table()["visits"].to_pylist():
        sheet.append([v])
    path = tmp_path / "book.xlsx"
    book.save(path)
    one = f"{path}#counts"  # one sheet: a table profile
    assert set(FIELDS) <= set(_columns(shape.profile(one, univariate=True))["visits"])
    assert not set(FIELDS) & set(_columns(shape.profile(one))["visits"])


def test_shape_profile_univariate_flag(tmp_path: Path) -> None:
    import pyarrow.parquet as pq

    src = tmp_path / "t.parquet"
    pq.write_table(_table(), src)
    deep, plain = tmp_path / "deep.shape", tmp_path / "plain.shape"
    assert main(["profile", str(src), "-o", str(deep), "--univariate"]) == 0
    assert main(["profile", str(src), "-o", str(plain)]) == 0
    assert set(FIELDS) <= set(_columns(shape.load(deep))["visits"])
    assert not any(set(FIELDS) & set(c) for c in _columns(shape.load(plain)).values())
