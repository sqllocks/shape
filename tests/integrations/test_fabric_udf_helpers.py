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
    with pytest.raises(udf.UserThrownError, match="tableName"):
        udf.profile_lakehouse_table(lh, "orders\n")
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


class _WideCursor:
    """A cursor over a large, wide table that counts the rows it hands out."""

    def __init__(self, n_rows: int, n_cols: int):
        self.n_rows, self.n_cols, self.fetched, self.description = n_rows, n_cols, 0, None

    def execute(self, sql: str):
        self.description = [(f"c{i}",) for i in range(self.n_cols)]
        self._next = 0

    def fetchmany(self, size: int = 1):
        stop = min(self._next + size, self.n_rows)
        rows = [tuple(range(self.n_cols))] * (stop - self._next)
        self._next = stop
        self.fetched += len(rows)
        return rows

    def fetchall(self):
        return self.fetchmany(self.n_rows)


def test_a_table_over_the_cell_limit_is_refused_before_it_is_read(monkeypatch):
    """#367: the cell guard stops the read; the table is not fetched in full first."""
    monkeypatch.setattr(udf, "MAX_TABLE_CELLS", 1_000)
    cursor = _WideCursor(n_rows=200_000, n_cols=10)
    conn = types.SimpleNamespace(cursor=lambda: cursor, close=lambda: None)
    lakehouse = types.SimpleNamespace(connectToSql=lambda: conn)
    with pytest.raises(udf.UserThrownError, match="cell"):
        udf.profile_lakehouse_table(lakehouse, "wide", max_rows=1_000_000)
    assert cursor.fetched <= 20_000


def test_the_row_past_max_rows_does_not_count_against_the_cell_limit(monkeypatch):
    """#367: exactly MAX_TABLE_CELLS cells in the first maxRows rows is allowed."""
    monkeypatch.setattr(udf, "MAX_TABLE_CELLS", 100)
    conn = types.SimpleNamespace(
        cursor=lambda: _WideCursor(n_rows=20, n_cols=2), close=lambda: None
    )
    lakehouse = types.SimpleNamespace(connectToSql=lambda: conn)
    out = udf.profile_lakehouse_table(lakehouse, "t", max_rows=50)
    assert out["rows"] == 20 and out["sampled"] is False
    monkeypatch.setattr(udf, "MAX_TABLE_CELLS", 40)
    out = udf.profile_lakehouse_table(lakehouse, "t", max_rows=20)
    assert out["rows"] == 20
    assert out["sampled"] is False  # the fake ignores TOP: 20 rows are not more than 20
    monkeypatch.setattr(udf, "MAX_TABLE_CELLS", 39)
    with pytest.raises(udf.UserThrownError, match="cell"):
        udf.profile_lakehouse_table(lakehouse, "t", max_rows=20)


# --------------------------------------------- #721: classified columns are withheld by default

N_PII = 60
EMAILS = [f"user{i}@example.com" for i in range(N_PII)]
SSNS = [f"123-45-{1000 + i}" for i in range(N_PII)]
RAW = {"email": (EMAILS[0], EMAILS[9]), "ssn": (SSNS[0], SSNS[-1])}


def _people(shift: float = 1.0) -> pd.DataFrame:
    rng = np.random.default_rng(int(shift))
    return pd.DataFrame(
        {
            "email": EMAILS,
            "ssn": SSNS,
            "balance": rng.normal(1000 * shift, 10, N_PII).round(4),
            "amount": [float(i % 7) * shift for i in range(N_PII)],
            "status": ["placed", "shipped", "returned"] * (N_PII // 3),
        }
    )


@pytest.fixture()
def pii(tmp_path: Path) -> _Lakehouse:
    (tmp_path / "p").mkdir()
    _people().to_csv(tmp_path / "p" / "p.csv", index=False)
    _people().to_parquet(tmp_path / "p" / "p1.parquet")
    _people(3.0).to_parquet(tmp_path / "p" / "p2.parquet")
    return _Lakehouse(tmp_path, _people())


def _profiles(lh: _Lakehouse, include_raw_values: bool = False) -> dict[str, dict]:
    return {
        "file": udf.profile_lakehouse_file(lh, "p/p.csv", include_raw_values=include_raw_values),
        "table": udf.profile_lakehouse_table(lh, "people", include_raw_values=include_raw_values),
        "frame": udf.profile_data_frame(_people(), include_raw_values=include_raw_values),
    }


def _no_raw_value_in(doc: object) -> None:
    text = json.dumps(doc)
    for value in (*EMAILS, *SSNS):
        assert value not in text


def test_profiles_withhold_min_and_max_of_classified_columns_by_default(pii):
    for name, out in _profiles(pii).items():
        cols = out["summary"]["columns"]
        for col in ("email", "ssn"):
            assert cols[col]["min"] is None and cols[col]["max"] is None, (name, col)
            assert cols[col]["redacted"] is True, (name, col)
            assert cols[col]["pattern"] == col  # the rest of the column is still reported
        _no_raw_value_in(out)
        json.dumps(out, allow_nan=False)


def test_profiles_return_raw_values_when_asked(pii):
    for name, out in _profiles(pii, include_raw_values=True).items():
        cols = out["summary"]["columns"]
        for col, (lo, hi) in RAW.items():
            assert (cols[col]["min"], cols[col]["max"]) == (lo, hi), (name, col)
            assert "redacted" not in cols[col]


def test_unclassified_columns_are_unchanged(pii):
    import shape

    expected = udf.json_safe(shape.profile(_people(), name="inline").summary())["columns"]
    for name, out in _profiles(pii).items():
        for col in ("amount", "status"):
            assert out["summary"]["columns"][col] == expected[col], (name, col)
            assert "redacted" not in out["summary"]["columns"][col]
    # and the redaction touches nothing but min, max and the marker
    email = _profiles(pii)["frame"]["summary"]["columns"]["email"]
    assert {k: v for k, v in email.items() if k not in ("min", "max", "redacted")} == {
        k: v for k, v in expected["email"].items() if k not in ("min", "max")
    }


PII_CONTRACT = {
    "columns": {
        "email": {"allowed_values": ["a@example.com"], "min": "zzz", "max": "a"},
        "ssn": {"min": "200", "max": "100"},
        "amount": {"max": 5},
        "status": {"allowed_values": ["placed", "shipped"]},
    }
}


def test_check_violations_carry_no_raw_value_of_classified_columns(pii):
    udf.profile_lakehouse_file(pii, "p/p1.parquet", "s/p1.shape")
    out = udf.check_profile(pii, "s/p1.shape", PII_CONTRACT)
    assert out["passed"] is False
    by_column = {}
    for v in out["violations"]:
        by_column.setdefault(v["column"], []).append(v)
    assert {"email", "ssn", "amount", "status"} <= set(by_column)
    for col in ("email", "ssn"):
        for v in by_column[col]:
            assert v["observed"] is None and v["redacted"] is True
    # unclassified violations keep their observed values
    assert {v["rule"]: v["observed"] for v in by_column["amount"]} == {"max": 6.0}
    assert by_column["status"][0]["observed"] == {"unexpected_values": ["returned"]}
    assert all("redacted" not in v for c in ("amount", "status") for v in by_column[c])
    _no_raw_value_in(out)
    with pytest.raises(udf.UserThrownError) as e:
        udf.check_profile(pii, "s/p1.shape", PII_CONTRACT, fail_on_violation=True)
    _no_raw_value_in(e.value.properties)
    _no_raw_value_in(e.value.message)
    raw = udf.check_profile(pii, "s/p1.shape", PII_CONTRACT, include_raw_values=True)
    assert {(v["column"], v["rule"]): v["observed"] for v in raw["violations"]}[
        ("ssn", "max")
    ] == SSNS[-1]


def test_diff_changes_carry_no_raw_value_of_classified_columns(pii):
    udf.profile_lakehouse_file(pii, "p/p1.parquet", "s/p1.shape")
    udf.profile_lakehouse_file(pii, "p/p2.parquet", "s/p2.shape")
    out = udf.diff_profiles(pii, "s/p1.shape", "s/p2.shape")
    balance = [c for c in out["changes"] if c["column"] == "balance"]  # nearly all distinct
    amount = [c for c in out["changes"] if c["column"] == "amount"]
    assert balance and amount
    for c in balance:
        assert c["baseline"] is None and c["current"] is None and c["redacted"] is True
    for c in amount:
        assert "redacted" not in c and c["baseline"] is not None and c["current"] is not None
    with pytest.raises(udf.UserThrownError) as e:
        udf.diff_profiles(pii, "s/p1.shape", "s/p2.shape", fail_on_drift=True)
    assert all(
        c["baseline"] is None for c in e.value.properties["changes"] if c["column"] == "balance"
    )
    raw = udf.diff_profiles(pii, "s/p1.shape", "s/p2.shape", include_raw_values=True)
    assert all(
        c["baseline"] is not None and "redacted" not in c
        for c in raw["changes"]
        if c["column"] == "balance"
    )
