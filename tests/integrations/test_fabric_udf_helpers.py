"""PF-03: ``shape.integrations.fabric.udf`` works with numpy, pyarrow and pandas only.

Needs no Fabric SDK, Delta or Spark, so the ``pure-wheel`` job runs this file against the
installed pure wheel with ``SHAPE_KERNEL=python``.
"""

from __future__ import annotations

import json
import subprocess
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from shape.integrations.fabric import udf

CONTRACT = {
    "row_count": {"min": 100},
    "columns": {
        "id": {"dtype": "integer", "nullable": False, "unique": True},
        "status": {"allowed_values": ["placed", "shipped"]},
    },
}


def _orders(day: int, n: int = 500) -> pd.DataFrame:
    rng = np.random.default_rng(day)
    status = ["placed", "shipped"] if day == 1 else ["placed", "shipped", "lost"]
    return pd.DataFrame(
        {
            "id": np.arange(n),
            "status": rng.choice(status, n),
            "amount": rng.lognormal(3, 0.5, n).round(2),
        }
    )


class _FileClient:
    def __init__(self, path: Path):
        self.path = path

    def get_file_properties(self):
        return types.SimpleNamespace(size=self.path.stat().st_size)

    def download_file(self):
        return types.SimpleNamespace(readall=lambda: self.path.read_bytes())

    def upload_data(self, data, overwrite=False):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(data)


class _Cursor:
    def __init__(self, frame: pd.DataFrame):
        self.frame, self.description, self.rows = frame, None, []

    def execute(self, sql: str):
        top = int(sql.split("TOP (")[1].split(")")[0])
        df = self.frame.head(top)
        self.description = [(c,) for c in df.columns]
        self.rows = list(df.itertuples(index=False, name=None))

    def fetchall(self):
        return self.rows


class _Conn:
    closed = False

    def __init__(self, frame):
        self.frame = frame

    def cursor(self):
        return _Cursor(self.frame)

    def close(self):
        self.closed = True


class _Lakehouse:
    def __init__(self, root: Path, table: pd.DataFrame):
        self.root, self.table, self.conn = root, table, None

    def connectToFiles(self):
        return types.SimpleNamespace(get_file_client=lambda p: _FileClient(self.root / p))

    def connectToSql(self):
        self.conn = _Conn(self.table)
        return self.conn


@pytest.fixture()
def lh(tmp_path: Path) -> _Lakehouse:
    (tmp_path / "d").mkdir()
    _orders(1).to_parquet(tmp_path / "d" / "day1.parquet")
    _orders(2).to_parquet(tmp_path / "d" / "day2.parquet")
    _orders(1).to_csv(tmp_path / "d" / "day1.csv", index=False)
    return _Lakehouse(tmp_path, _orders(1))


def test_profile_file_and_save(lh):
    out = udf.profile_lakehouse_file(lh, "Files/d/day1.parquet", "s/day1.shape")
    assert out["rows"] == 500 and out["sampled"] is False and out["outputPath"] == "s/day1.shape"
    json.dumps(out, allow_nan=False)
    assert len(json.dumps(out)) < 1_000_000
    assert udf.profile_lakehouse_file(lh, "d/day1.csv")["rows"] == 500


def test_size_guard_is_a_user_error_pointing_to_the_notebook(lh):
    big = lh.root / "d" / "big.csv"
    pd.DataFrame({"a": np.arange(300_000), "b": ["x" * 20] * 300_000}).to_csv(big, index=False)
    with pytest.raises(udf.UserThrownError) as e:
        udf.profile_lakehouse_file(lh, "d/big.csv", max_megabytes=1)
    assert "notebook" in e.value.message.lower()
    assert e.value.properties["limitMegabytes"] == 1.0


@pytest.mark.parametrize("path", ["../x", "", "d/missing.parquet", "d/x.txt"])
def test_bad_paths_raise_user_errors(lh, path):
    (lh.root / "d" / "x.txt").write_text("hi")
    with pytest.raises(udf.UserThrownError):
        udf.profile_lakehouse_file(lh, path)


def test_table_cap_sampled_and_unsafe_names(lh):
    out = udf.profile_lakehouse_table(lh, "dbo.orders", max_rows=100)
    assert out["rows"] == 100 and out["sampled"] is True and out["maxRows"] == 100
    assert lh.conn.closed
    with pytest.raises(udf.UserThrownError, match="tableName"):
        udf.profile_lakehouse_table(lh, "a; DROP TABLE b")
    with pytest.raises(udf.UserThrownError, match="maxRows"):
        udf.profile_lakehouse_table(lh, "orders", max_rows=0)


def test_check_and_diff_with_failure_modes(lh):
    udf.profile_lakehouse_file(lh, "d/day1.parquet", "s/1.shape")
    udf.profile_lakehouse_file(lh, "d/day2.parquet", "s/2.shape")
    assert udf.check_profile(lh, "s/1.shape", CONTRACT)["passed"] is True
    bad = udf.check_profile(lh, "s/2.shape", CONTRACT)
    assert bad["passed"] is False and bad["violationCount"] >= 1
    with pytest.raises(udf.UserThrownError, match="violation") as e:
        udf.check_profile(lh, "s/2.shape", CONTRACT, fail_on_violation=True)
    assert e.value.properties["violationCount"] == bad["violationCount"]
    assert udf.diff_profiles(lh, "s/1.shape", "s/1.shape")["drifted"] is False
    d = udf.diff_profiles(lh, "s/1.shape", "s/2.shape")
    assert d["drifted"] is True
    with pytest.raises(udf.UserThrownError, match="Drift detected"):
        udf.diff_profiles(lh, "s/1.shape", "s/2.shape", fail_on_drift=True)


def test_profile_data_frame_and_json_safety():
    assert udf.profile_data_frame(_orders(1, 50))["rows"] == 50
    with pytest.raises(udf.UserThrownError):
        udf.profile_data_frame(pd.DataFrame())
    assert udf.json_safe({"a": float("nan"), "b": np.int64(3)}) == {"a": None, "b": 3}
    out = udf.bounded({"summary": {"x": "y" * 2_000_000}})
    assert out["summary"] is None and out["summaryOmitted"] is True


def test_imports_without_the_fabric_sdk():
    code = (
        "import sys; sys.modules['fabric'] = None; "
        "from shape.integrations.fabric import udf; "
        "assert udf.UserThrownError.__module__ == 'shape.integrations.fabric.udf'; "
        "e = udf.UserThrownError('m', {'k': 1}); "
        "assert (e.message, e.properties) == ('m', {'k': 1})"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
