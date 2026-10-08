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
        # like the SQL analytics endpoint: tables live in a schema, and an unqualified name does
        # not resolve (42S02, the live dry run's error for FROM [orders_day1])
        if key not in self.tables:
            raise RuntimeError(f"('42S02', \"Invalid object name '{key}'. (208)\")")
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
    return FakeLakehouse(root, {"dbo.orders_day1": d1, "dbo.orders_day2": d2})


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
        "includeRawValues": False,
    }
    assert params("profileLakehouseTable") == {
        "lakehouse": "<req>",
        "tableName": "<req>",
        "maxRows": 1000000,
        "outputPath": "",
        "includeRawValues": False,
    }
    assert params("checkProfile") == {
        "lakehouse": "<req>",
        "profilePath": "<req>",
        "contract": "<req>",
        "failOnViolation": False,
        "includeRawValues": False,
    }
    assert params("diffProfiles") == {
        "lakehouse": "<req>",
        "baselinePath": "<req>",
        "currentPath": "<req>",
        "failOnDrift": False,
        "includeRawValues": False,
    }
    assert params("profileDataFrame") == {"data": "<req>", "includeRawValues": False}


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
    # F-9: no schema given means dbo (the endpoint does not resolve [orders_day1] alone)
    assert lh.sql_log == ["SELECT TOP (1000001) * FROM [dbo].[orders_day1]"]
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


# ------------------------------------- #721: classified columns withheld unless includeRawValues


# what the safe-profile gate classifies in make_orders: email by its pattern, customer_id and
# amount because nearly every value is distinct
CLASSIFIED = {"email", "customer_id", "amount"}


def test_the_fixture_columns_the_gate_classifies():
    import shape
    from shape.bridge.handlers.flow import classified_columns

    for day in (1, 2):
        names = classified_columns(shape.profile(make_orders(day), name="t"))
        assert {n for n in names if "." not in n} == CLASSIFIED


def _email(out: dict) -> dict:
    return out["summary"]["columns"]["email"]


def test_profiles_withhold_classified_min_max_unless_include_raw_values(lh):
    calls = {
        "profileLakehouseFile": {"lakehouse": lh, "filePath": "demo/orders_day1.parquet"},
        "profileLakehouseTable": {"lakehouse": lh, "tableName": "orders_day1"},
        "profileDataFrame": {"data": make_orders(1)},
    }
    for name, kwargs in calls.items():
        safe = raw(name)(**kwargs)
        assert _email(safe)["min"] is None and _email(safe)["max"] is None, name
        assert _email(safe)["redacted"] is True, name
        assert "@example.com" not in json.dumps(safe), name
        full = raw(name)(**kwargs, includeRawValues=True)
        assert _email(full)["min"].endswith("@example.com") and "redacted" not in _email(full)
        # the gate also classifies the nearly-all-distinct customer_id and amount; status is
        # unclassified and the same either way
        cols, full_cols = safe["summary"]["columns"], full["summary"]["columns"]
        assert {c for c, v in cols.items() if v.get("redacted")} == CLASSIFIED, name
        for col in CLASSIFIED:
            assert full_cols[col]["min"] is not None and "redacted" not in full_cols[col]
        assert cols["status"] == full_cols["status"], name


def test_check_and_diff_withhold_classified_values_unless_include_raw_values(saved):
    bad = raw("checkProfile")(lakehouse=saved, profilePath="shape/day2.shape", contract=CONTRACT)
    email = [v for v in bad["violations"] if v["column"] == "email"]
    assert email and all(v["observed"] is None and v["redacted"] for v in email)
    status = next(v for v in bad["violations"] if v["column"] == "status")
    assert status["observed"] is not None and "redacted" not in status
    full = raw("checkProfile")(
        lakehouse=saved, profilePath="shape/day2.shape", contract=CONTRACT, includeRawValues=True
    )
    assert next(v for v in full["violations"] if v["column"] == "email")["observed"] > 0.1
    paths = {"baselinePath": "shape/day1.shape", "currentPath": "shape/day2.shape"}
    d = raw("diffProfiles")(lakehouse=saved, **paths)
    assert {c["column"] for c in d["changes"]} >= {"email", "amount", "status"}
    for c in d["changes"]:
        if c["column"] in CLASSIFIED:
            assert c["baseline"] is None and c["current"] is None and c["redacted"] is True
        else:
            assert "redacted" not in c and c["baseline"] is not None
    d_full = raw("diffProfiles")(lakehouse=saved, **paths, includeRawValues=True)
    assert all("redacted" not in c for c in d_full["changes"])


def test_pipeline_b_udf_gate_still_fails_on_day2_for_the_same_reason(lh):
    """Pipeline (b) ``shape_gate_udf`` with its own parameters: profileLakehouseFile, then
    checkProfile with failOnViolation. Day 1 passes; day 2 raises naming the email null rate
    and the new status value, as before the safe default."""
    from fabric_helpers import PIPELINES

    p = json.loads(
        (PIPELINES / "shape_gate_udf.DataPipeline" / "pipeline-content.json").read_text()
    )
    acts = {a["name"]: a["typeProperties"] for a in p["properties"]["activities"]}

    def run(day: int) -> dict:
        pipeline = {"filePath": f"demo/orders_day{day}.parquet", "profilePath": f"g/{day}.shape"}

        def args(name: str) -> dict:
            out = {}
            for k, v in acts[name]["parameters"].items():
                value = v["value"]
                if isinstance(value, dict) and value.get("type") == "Expression":
                    key = value["value"].removeprefix("@pipeline().parameters.")
                    value = CONTRACT if key == "contract" else pipeline[key]
                out[k] = value
            return out

        raw(acts["ProfileFile"]["functionName"])(lakehouse=lh, **args("ProfileFile"))
        return raw(acts["CheckContract"]["functionName"])(lakehouse=lh, **args("CheckContract"))

    assert run(1)["passed"] is True
    with pytest.raises(fn.UserThrownError) as e:
        run(2)
    assert "email: max_null_rate" in e.value.message
    assert "status: allowed_values" in e.value.message
    assert {(v["column"], v["rule"]) for v in e.value.properties["violations"]} == {
        ("email", "max_null_rate"),
        ("status", "allowed_values"),
    }


# ------------------------------------------------------- DEMO-LIVE F-6: the connection alias


def test_every_connection_alias_is_the_literal_the_portal_reads():
    """The portal reads the ``@udf.connection`` alias from ``function_app.py`` statically, so it
    must be a string literal (an imported constant showed no connection), and that literal must
    be the alias the helpers document, ``shape.integrations.fabric.udf.LAKEHOUSE_ALIAS``."""
    tree = ast.parse((UDF_DIR / "function_app.py").read_text(encoding="utf-8"))
    aliases = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for deco in node.decorator_list:
            if isinstance(deco, ast.Call) and ast.unparse(deco.func) == "udf.connection":
                first = deco.args[0]
                assert isinstance(first, ast.Constant) and isinstance(first.value, str), node.name
                aliases.append((node.name, first.value))
    assert [name for name, _ in aliases] == [
        "profileLakehouseFile",
        "profileLakehouseTable",
        "checkProfile",
        "diffProfiles",
    ]
    assert {alias for _, alias in aliases} == {shape_udf.LAKEHOUSE_ALIAS} == {"shapeLakehouse"}
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert "LAKEHOUSE_ALIAS" not in imported


def test_the_requirements_pin_the_working_set_and_upload_the_wheel():
    """F-6: the UDF runtime is Python 3.11; the owner's working set is numpy 2.4.6, pyarrow 19.0.1
    (fabric-user-data-functions 1.0.142 requires >=19.0.1,<20) and pandas 3.0.6. F-4: the Shape
    wheel is a private library uploaded from a file, never resolved by name."""
    text = (UDF_DIR / "requirements.md").read_text(encoding="utf-8")
    assert "**Python 3.11**" in text
    for row in ("| `numpy` | `2.4.6` |", "| `pyarrow` | `19.0.1` |", "| `pandas` | `3.0.6` |"):
        assert row in text, row
    assert "pyarrow>=19.0.1,<20" in text
    assert "Never list `sqllocks-shape`" in text and "Add from local" in text


# ------------------------------------------- DEMO-LIVE F-7: rules a safe capture cannot answer


@pytest.fixture()
def safe_saved(saved):
    """A notebook artifact: ``shape.save`` with the default safe capture, which leaves out the
    values ``min`` / ``max`` / ``allowed_values`` need (W1-11)."""
    import shape

    profile = shape.profile(make_orders(1), name="orders_day1")
    shape.save(profile, str(saved.root / "shape" / "notebook_day1.shape"))
    return saved


def test_a_safe_capture_profile_says_what_it_could_not_check(safe_saved):
    out = raw("checkProfile")(
        lakehouse=safe_saved, profilePath="shape/notebook_day1.shape", contract=CONTRACT
    )
    assert out["passed"] is False and out["violationCount"] == 0
    assert out["notEvaluableCount"] == len(out["notEvaluable"]) >= 1
    for gap in out["notEvaluable"]:
        assert {"column", "rule", "reason"} <= set(gap) and "captured safe" in gap["reason"]
    assert {(g["column"], g["rule"]) for g in out["notEvaluable"]} >= {("amount", "max")}
    # the full capture profileLakehouseFile writes has nothing left out
    full = raw("checkProfile")(
        lakehouse=safe_saved, profilePath="shape/day1.shape", contract=CONTRACT
    )
    assert full["passed"] is True and full["notEvaluableCount"] == 0 and full["notEvaluable"] == []


def test_fail_on_violation_with_only_unevaluable_rules_says_why(safe_saved):
    with pytest.raises(fn.UserThrownError) as e:
        raw("checkProfile")(
            lakehouse=safe_saved,
            profilePath="shape/notebook_day1.shape",
            contract=CONTRACT,
            failOnViolation=True,
        )
    message = e.value.message
    assert message.startswith("Contract check failed with 0 violation(s);")
    assert "not evaluable: " in message and "amount: max" in message
    assert "full-capture .shape" in message and "profileLakehouseFile" in message
    props = e.value.properties
    assert props["violationCount"] == 0 and props["violations"] == []
    assert props["notEvaluableCount"] == len(props["notEvaluable"]) >= 1


def test_the_unevaluable_entries_are_bounded(safe_saved, monkeypatch):
    monkeypatch.setattr(shape_udf, "MAX_LISTED", 1)
    out = raw("checkProfile")(
        lakehouse=safe_saved, profilePath="shape/notebook_day1.shape", contract=CONTRACT
    )
    assert out["notEvaluableCount"] == 2  # amount: min and max
    assert len(out["notEvaluable"]) == 1 and out["truncated"] is True
    with pytest.raises(fn.UserThrownError) as e:
        raw("checkProfile")(
            lakehouse=safe_saved,
            profilePath="shape/notebook_day1.shape",
            contract=CONTRACT,
            failOnViolation=True,
        )
    assert len(e.value.properties["notEvaluable"]) == 1


# ------------------------------------------------ DEMO-LIVE F-8: bounded diff and check errors


@pytest.fixture()
def many_new_values(lh):
    """Day 2 multiplies a 400-value float column by 1.4: every value is a new category, so the
    ``new_categorical_values`` change lists hundreds of values on each side."""
    rng = np.random.default_rng(1)
    values = np.round(rng.uniform(1, 500, size=400), 2)
    day1 = pd.DataFrame({"total": rng.choice(values, 5000)})
    day2 = pd.DataFrame({"total": day1["total"] * 1.4})
    for name, frame in (("d1", day1), ("d2", day2)):
        frame.to_parquet(lh.root / "demo" / f"{name}.parquet")
        raw("profileLakehouseFile")(
            lakehouse=lh, filePath=f"demo/{name}.parquet", outputPath=f"shape/{name}.shape"
        )
    return lh


def _new_values(changes):
    return next(c for c in changes if c["kind"] == "new_categorical_values")


def test_each_change_keeps_only_the_first_values_and_says_how_many(many_new_values):
    out = raw("diffProfiles")(
        lakehouse=many_new_values,
        baselinePath="shape/d1.shape",
        currentPath="shape/d2.shape",
        includeRawValues=True,
    )
    change = _new_values(out["changes"])
    assert len(change["current"]) == shape_udf.MAX_VALUES
    assert change["currentCount"] > shape_udf.MAX_VALUES
    assert change["baselineCount"] > shape_udf.MAX_VALUES
    assert change["valuesTruncated"] is True
    assert len(json.dumps(out)) < 20_000


def test_the_fail_on_drift_error_is_bounded_like_the_result(many_new_values, monkeypatch):
    with pytest.raises(fn.UserThrownError) as e:
        raw("diffProfiles")(
            lakehouse=many_new_values,
            baselinePath="shape/d1.shape",
            currentPath="shape/d2.shape",
            failOnDrift=True,
            includeRawValues=True,
        )
    props = e.value.properties
    change = _new_values(props["changes"])
    assert len(change["current"]) == shape_udf.MAX_VALUES and change["valuesTruncated"] is True
    assert len(json.dumps(props)) < 20_000
    # and the result bound applies to the payload: past it, the changes are dropped, not sent
    monkeypatch.setattr(shape_udf, "MAX_RESULT_BYTES", 500)
    with pytest.raises(fn.UserThrownError) as e:
        raw("diffProfiles")(
            lakehouse=many_new_values,
            baselinePath="shape/d1.shape",
            currentPath="shape/d2.shape",
            failOnDrift=True,
            includeRawValues=True,
        )
    props = e.value.properties
    assert props["changes"] is None and props["changesOmitted"] is True
    assert props["changeCount"] >= 2


def test_cap_values_leaves_short_values_alone():
    entry = {"column": "c", "kind": "k", "baseline": [1, 2], "current": {"a": 1}, "score": 0.5}
    assert shape_udf.cap_values(entry) == entry
    cut = shape_udf.cap_values({"current": list(range(30)), "baseline": dict.fromkeys("abc", 1)}, 2)
    assert cut == {
        "current": [0, 1],
        "currentCount": 30,
        "baseline": {"a": 1, "b": 1},
        "baselineCount": 3,
        "valuesTruncated": True,
    }


# ----------------------------------------------- DEMO-LIVE F-9: the SQL endpoint's dbo schema


@pytest.mark.parametrize(
    "name, quoted",
    [
        ("orders_day1", "[dbo].[orders_day1]"),
        ("dbo.orders_day1", "[dbo].[orders_day1]"),
        ("sales.orders", "[sales].[orders]"),
        ("_t1", "[dbo].[_t1]"),
    ],
)
def test_names_without_a_schema_get_dbo(name, quoted):
    assert shape_udf.sql_table_name(name) == quoted


def test_a_closing_bracket_is_doubled_inside_a_quoted_part():
    # unreachable through profileLakehouseTable (the name pattern refuses it): defence in depth
    assert shape_udf.sql_table_name("a]b") == "[dbo].[a]]b]"


def test_the_unqualified_and_the_dbo_name_read_the_same_table(lh):
    plain = raw("profileLakehouseTable")(lakehouse=lh, tableName="orders_day1")
    dbo = raw("profileLakehouseTable")(lakehouse=lh, tableName="dbo.orders_day1")
    assert plain["rows"] == dbo["rows"] == 2000
    assert lh.sql_log == ["SELECT TOP (1000001) * FROM [dbo].[orders_day1]"] * 2
    assert plain["source"] == "orders_day1" and dbo["source"] == "dbo.orders_day1"
