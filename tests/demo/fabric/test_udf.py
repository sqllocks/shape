"""DM-06: call each User Data Function directly with fake Fabric lakehouse clients."""

from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import re
import types
from pathlib import Path

import fabric.functions as fn
import numpy as np
import pandas as pd
import pytest
from fabric_helpers import CONTRACT, UDF_DIR, make_orders

from shape.integrations.fabric import udf as shape_udf


def _load_module():
    spec = importlib.util.spec_from_file_location("function_app", UDF_DIR / "function_app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_APP = None


def app():
    """Load function_app lazily, inside the module's shape_api() context."""
    global _APP
    if _APP is None:
        _APP = _load_module()
    return _APP


NAMES = [
    "profileLakehouseFile",
    "profileLakehouseTable",
    "checkProfile",
    "diffProfiles",
    "profileDataFrame",
]


def raw(name: str):
    """The user's function without the Fabric HTTP wrapper."""
    return inspect.unwrap(getattr(app(), name)._function.get_user_function())


def ast_returns(name: str) -> str | None:
    """Declared return annotation, read from source (the SDK rewrites runtime annotations)."""
    tree = ast.parse((UDF_DIR / "function_app.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.unparse(node.returns) if node.returns else None
    raise AssertionError(name)


def signature(name: str) -> tuple[dict[str, object], object]:
    """(parameter -> default or '<req>', return annotation), read from the real function.

    ``inspect.signature`` would show the SDK wrapper's ``__signature__`` instead.
    """
    f = raw(name)
    names = f.__code__.co_varnames[: f.__code__.co_argcount]
    defaults = f.__defaults__ or ()
    filled = ["<req>"] * (len(names) - len(defaults)) + list(defaults)
    return dict(zip(names, filled, strict=True)), f.__annotations__.get("return")


# ------------------------------------------------------------------- fake clients


class FakeFileClient:
    def __init__(self, path: Path):
        self.path = path

    def get_file_properties(self):
        return types.SimpleNamespace(size=self.path.stat().st_size)

    def download_file(self):
        return types.SimpleNamespace(readall=lambda: self.path.read_bytes())

    def upload_data(self, data, overwrite=False):
        assert overwrite is True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(data)


class FakeFiles:
    def __init__(self, root: Path):
        self.root = root

    def get_file_client(self, path: str):
        assert not path.startswith("/") and ".." not in path
        return FakeFileClient(self.root / path)


class FakeCursor:
    def __init__(self, tables: dict[str, pd.DataFrame], log: list[str]):
        self.tables, self.log, self._rows, self.description = tables, log, [], None

    def execute(self, sql: str):
        self.log.append(sql)
        m = re.fullmatch(r"SELECT TOP \((\d+)\) \* FROM ((?:\[\w+\]\.)?\[\w+\])", sql)
        assert m, f"unexpected SQL: {sql}"
        key = m.group(2).replace("[", "").replace("]", "")
        if key not in self.tables:
            raise RuntimeError(f"Invalid object name '{key}'")
        df = self.tables[key].head(int(m.group(1)))
        self.description = [(c, None, None, None, None, None, None) for c in df.columns]
        self._rows = list(df.itertuples(index=False, name=None))

    def fetchall(self):
        return self._rows


class FakeConn:
    def __init__(self, tables, log):
        self.tables, self.log, self.closed = tables, log, False

    def cursor(self):
        return FakeCursor(self.tables, self.log)

    def close(self):
        self.closed = True


class FakeLakehouse:
    def __init__(self, root: Path, tables: dict[str, pd.DataFrame] | None = None):
        self.root, self.tables, self.sql_log, self.conns = root, tables or {}, [], []

    def connectToFiles(self):
        return FakeFiles(self.root)

    def connectToSql(self):
        c = FakeConn(self.tables, self.sql_log)
        self.conns.append(c)
        return c


@pytest.fixture()
def lh(tmp_path: Path) -> FakeLakehouse:
    root = tmp_path / "Files"
    (root / "demo").mkdir(parents=True)
    d1, d2 = make_orders(1), make_orders(2)
    d1.to_parquet(root / "demo" / "orders_day1.parquet")
    d2.to_parquet(root / "demo" / "orders_day2.parquet")
    d1.to_csv(root / "demo" / "orders_day1.csv", index=False)
    d1.to_json(root / "demo" / "orders_day1.jsonl", orient="records", lines=True)
    return FakeLakehouse(root, {"orders_day1": d1, "dbo.orders_day2": d2})


# ------------------------------------------------------------------- registration


def test_all_functions_registered_with_camel_case_and_dict_return():
    for name in NAMES:
        builder = getattr(app(), name)
        assert type(builder).__name__ == "FunctionBuilder"
        params, _ = signature(name)
        assert ast_returns(name) == "dict"
        for p in params:
            assert re.fullmatch(r"[a-z]+([A-Z][a-z0-9]*)*", p), f"{name}: {p} is not camelCase"


def test_signatures_match_plan():
    def params(n):
        return signature(n)[0]

    assert params("profileLakehouseFile") == {
        "lakehouse": "<req>",
        "filePath": "<req>",
        "outputPath": "",
        "maxMegabytes": 50,
    }
    assert params("profileLakehouseTable") == {
        "lakehouse": "<req>",
        "tableName": "<req>",
        "maxRows": 1000000,
        "outputPath": "",
    }
    assert params("checkProfile") == {
        "lakehouse": "<req>",
        "profilePath": "<req>",
        "contract": "<req>",
        "failOnViolation": False,
    }
    assert params("diffProfiles") == {
        "lakehouse": "<req>",
        "baselinePath": "<req>",
        "currentPath": "<req>",
        "failOnDrift": False,
    }
    assert params("profileDataFrame") == {"data": "<req>"}


# ----------------------------------------------------------------------- files


@pytest.mark.parametrize("ext", ["parquet", "csv", "jsonl"])
def test_profile_file_formats(lh, ext):
    out = raw("profileLakehouseFile")(lakehouse=lh, filePath=f"demo/orders_day1.{ext}")
    assert out["rows"] == 2000 and out["columns"] == 4 and out["sampled"] is False
    assert out["summary"]["row_count"] == 2000
    assert len(json.dumps(out)) < 1_000_000
    json.dumps(out, allow_nan=False)


def test_profile_file_writes_shape_artifact_and_accepts_files_prefix(lh):
    out = raw("profileLakehouseFile")(
        lakehouse=lh, filePath="/Files/demo/orders_day1.parquet", outputPath="shape/day1.shape"
    )
    assert out["outputPath"] == "shape/day1.shape"
    import shape

    assert shape.load(str(lh.root / "shape" / "day1.shape")).summary()["row_count"] == 2000


def test_size_guard_raises_user_thrown_error_recommending_notebook(lh):
    big = lh.root / "demo" / "big.csv"
    pd.DataFrame({"a": np.arange(300_000), "b": ["x" * 20] * 300_000}).to_csv(big, index=False)
    assert big.stat().st_size > 1_000_000
    with pytest.raises(fn.UserThrownError) as e:
        raw("profileLakehouseFile")(lakehouse=lh, filePath="demo/big.csv", maxMegabytes=1)
    assert "notebook" in e.value.message.lower() and "1 MB" in e.value.message
    assert e.value.properties["limitMegabytes"] == 1.0
    # and it passes with a larger cap
    assert (
        raw("profileLakehouseFile")(lakehouse=lh, filePath="demo/big.csv", maxMegabytes=50)["rows"]
        == 300_000
    )


@pytest.mark.parametrize(
    "path, text",
    [
        ("demo/missing.parquet", "Cannot read"),
        ("demo/orders_day1.xlsx", "Cannot read"),
        ("../etc/passwd", "Invalid lakehouse path"),
        ("", "Invalid lakehouse path"),
    ],
)
def test_file_errors_are_user_thrown(lh, path, text):
    with pytest.raises(fn.UserThrownError, match=text):
        raw("profileLakehouseFile")(lakehouse=lh, filePath=path)


def test_unsupported_type_and_bad_content(lh):
    (lh.root / "demo" / "x.txt").write_text("hello")
    with pytest.raises(fn.UserThrownError, match="Unsupported file type"):
        raw("profileLakehouseFile")(lakehouse=lh, filePath="demo/x.txt")
    (lh.root / "demo" / "bad.parquet").write_bytes(b"not parquet")
    with pytest.raises(fn.UserThrownError, match="Could not parse"):
        raw("profileLakehouseFile")(lakehouse=lh, filePath="demo/bad.parquet")


def test_wide_file_result_stays_under_1mb(lh):
    wide = pd.DataFrame(np.random.default_rng(1).normal(size=(200, 500)))
    wide.columns = [f"col{i}" for i in range(500)]
    wide.to_parquet(lh.root / "demo" / "wide.parquet")
    out = raw("profileLakehouseFile")(lakehouse=lh, filePath="demo/wide.parquet")
    assert out["columns"] == 500 and len(json.dumps(out)) < 1_000_000


def test_max_megabytes_validation(lh):
    with pytest.raises(fn.UserThrownError, match="maxMegabytes"):
        raw("profileLakehouseFile")(lakehouse=lh, filePath="demo/orders_day1.csv", maxMegabytes=0)


# ----------------------------------------------------------------------- tables


def test_profile_table_full(lh):
    out = raw("profileLakehouseTable")(lakehouse=lh, tableName="orders_day1")
    assert out["rows"] == 2000 and out["sampled"] is False
    assert lh.sql_log == ["SELECT TOP (1000001) * FROM [orders_day1]"]
    assert lh.conns[0].closed


def test_profile_table_hits_row_cap_and_reports_sampled(lh):
    out = raw("profileLakehouseTable")(lakehouse=lh, tableName="dbo.orders_day2", maxRows=500)
    assert out["rows"] == 500 and out["sampled"] is True and out["maxRows"] == 500
    assert lh.sql_log == ["SELECT TOP (501) * FROM [dbo].[orders_day2]"]


def test_profile_table_writes_artifact(lh):
    raw("profileLakehouseTable")(lakehouse=lh, tableName="orders_day1", outputPath="shape/t.shape")
    assert (lh.root / "shape" / "t.shape").exists()


@pytest.mark.parametrize("bad", ["orders; DROP TABLE x", "a.b.c", "[orders]", "orders--", ""])
def test_profile_table_rejects_unsafe_names(lh, bad):
    with pytest.raises(fn.UserThrownError, match="tableName"):
        raw("profileLakehouseTable")(lakehouse=lh, tableName=bad)
    assert lh.sql_log == []


@pytest.mark.parametrize("rows", [0, -1, 5_000_001])
def test_profile_table_row_cap_guard(lh, rows):
    with pytest.raises(fn.UserThrownError, match="maxRows"):
        raw("profileLakehouseTable")(lakehouse=lh, tableName="orders_day1", maxRows=rows)


def test_profile_table_missing_table_and_connection_closed(lh):
    with pytest.raises(fn.UserThrownError, match="Could not read table"):
        raw("profileLakehouseTable")(lakehouse=lh, tableName="nope")
    assert lh.conns[0].closed


# ----------------------------------------------------------- check / diff / inline


@pytest.fixture()
def saved(lh):
    for day in (1, 2):
        raw("profileLakehouseFile")(
            lakehouse=lh,
            filePath=f"demo/orders_day{day}.parquet",
            outputPath=f"shape/day{day}.shape",
        )
    return lh


def test_check_profile_pass_and_fail(saved):
    ok = raw("checkProfile")(lakehouse=saved, profilePath="shape/day1.shape", contract=CONTRACT)
    assert ok["passed"] is True and ok["violations"] == []
    bad = raw("checkProfile")(lakehouse=saved, profilePath="shape/day2.shape", contract=CONTRACT)
    assert bad["passed"] is False
    assert {"email", "status"} <= {v["column"] for v in bad["violations"]}


def test_check_profile_fail_on_violation_raises_with_violations(saved):
    with pytest.raises(fn.UserThrownError) as e:
        raw("checkProfile")(
            lakehouse=saved, profilePath="shape/day2.shape", contract=CONTRACT, failOnViolation=True
        )
    assert "violation" in e.value.message and "email" in e.value.message
    assert e.value.properties["violationCount"] >= 2
    assert {v["rule"] for v in e.value.properties["violations"]} >= {"allowed_values"}
    # passing data does not raise even with the flag set
    assert raw("checkProfile")(
        lakehouse=saved, profilePath="shape/day1.shape", contract=CONTRACT, failOnViolation=True
    )["passed"]


def test_check_profile_bad_inputs(saved):
    with pytest.raises(fn.UserThrownError, match="Cannot read"):
        raw("checkProfile")(lakehouse=saved, profilePath="shape/none.shape", contract=CONTRACT)
    (saved.root / "shape" / "junk.shape").write_text("not json")
    with pytest.raises(fn.UserThrownError, match="not a readable .shape"):
        raw("checkProfile")(lakehouse=saved, profilePath="shape/junk.shape", contract=CONTRACT)


def test_diff_profiles(saved):
    same = raw("diffProfiles")(
        lakehouse=saved, baselinePath="shape/day1.shape", currentPath="shape/day1.shape"
    )
    assert same["drifted"] is False and same["changes"] == []
    d = raw("diffProfiles")(
        lakehouse=saved, baselinePath="shape/day1.shape", currentPath="shape/day2.shape"
    )
    assert d["drifted"] is True and {"email", "status", "amount"} <= {
        c["column"] for c in d["changes"]
    }
    with pytest.raises(fn.UserThrownError, match="Drift detected") as e:
        raw("diffProfiles")(
            lakehouse=saved,
            baselinePath="shape/day1.shape",
            currentPath="shape/day2.shape",
            failOnDrift=True,
        )
    assert e.value.properties["changeCount"] == len(d["changes"])


def test_profile_data_frame(lh):
    out = raw("profileDataFrame")(data=make_orders(1, 100))
    assert out["rows"] == 100 and out["source"] == "inline"
    with pytest.raises(fn.UserThrownError):
        raw("profileDataFrame")(data=pd.DataFrame())


def test_nan_and_numpy_values_are_json_safe():
    out = shape_udf.json_safe({"a": float("nan"), "b": np.int64(3), "c": [np.float64("inf"), 1.5]})
    assert out == {"a": None, "b": 3, "c": [None, 1.5]}
    json.dumps(out, allow_nan=False)


def test_oversized_result_drops_summary():
    out = shape_udf.bounded({"summary": {"x": "y" * 2_000_000}, "rows": 1})
    assert out["summary"] is None and out["summaryOmitted"] is True


# ---------------------------------------------------------------------- timings


def test_timings_are_logged_and_returned(lh, caplog, capsys):
    with caplog.at_level("INFO", logger="shape.udf"):
        results = {
            "file": raw("profileLakehouseFile")(lakehouse=lh, filePath="demo/orders_day1.parquet"),
            "table": raw("profileLakehouseTable")(lakehouse=lh, tableName="orders_day1"),
            "inline": raw("profileDataFrame")(data=make_orders(1)),
        }
    lines = [r.getMessage() for r in caplog.records if "elapsed_seconds" in r.getMessage()]
    assert len(lines) == 3
    with capsys.disabled():
        for name, r in results.items():
            print(
                f"\n[udf timing] {name}: rows={r['rows']} "
                f"elapsed={r['elapsedSeconds']}s (fixture data)"
            )
    assert all(r["elapsedSeconds"] < 30 for r in results.values())
