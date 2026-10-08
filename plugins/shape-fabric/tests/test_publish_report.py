"""W5-09 item 5: ``shape publish-report``: a profile history as drift tables and a semantic model.

The history is planted: a :class:`~shape.generation.drift_plan.DriftPlan` changes the ``orders``
feed on known days, one profile is taken on some of them, and the report must hold every planted
change (``DriftPlan.expected_changes``) in ``fact_drift_change``.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import warnings
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest

import shape
from shape.cli.main import main
from shape.generation.drift_plan import DriftPlan
from shape.generation.schema import GenSchema

pytestmark = [pytest.mark.contract, pytest.mark.filterwarnings("ignore::UserWarning")]

HISTORY = json.loads(
    (Path(__file__).parent / "data" / "drift_history.json").read_text(encoding="utf-8")
)
SCHEMA = GenSchema.from_dict(HISTORY["schema"])
PLAN = DriftPlan(HISTORY["events"], start="2026-03-01", days=14)
DAYS = [0, 5, 10, 12]  # the days a profile is taken on
ROWS = 3000


def profile_of(day: int):
    table = PLAN.generate_day(SCHEMA, day, row_counts={"orders": ROWS})["orders"]
    return shape.profile(table, name="orders")


@pytest.fixture(scope="module")
def history(tmp_path_factory) -> list[Path]:
    """One ``.shape`` file per day, named by its date."""
    base = tmp_path_factory.mktemp("history")
    paths = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for day in DAYS:
            path = base / f"orders-{PLAN.date_of(day).isoformat()}.shape"
            shape.save(
                profile_of(day), str(path), capture="full"
            )  # W1-11: the report reads full profiles
            paths.append(path)
    return paths


@pytest.fixture
def run(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def go(*argv):
        code = main(list(argv))
        out = capsys.readouterr()
        return code, out.out, out.err

    return go


@pytest.fixture(scope="module")
def report(tmp_path_factory, history) -> Path:
    out = tmp_path_factory.mktemp("report")
    assert main(["publish-report", *map(str, history), "-o", str(out)]) == 0
    return out


def table_of(directory: Path, name: str, ext: str = "parquet") -> pa.Table:
    return pq.read_table(directory / "data" / f"{name}.{ext}")


def rows_of(directory: Path, name: str) -> list[dict]:
    return table_of(directory, name).to_pylist()


def report_json(directory: Path) -> dict:
    return json.loads((directory / "report.json").read_text(encoding="utf-8"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---- the tables --------------------------------------------------------------------------------


def test_the_report_writes_the_star_tables_the_model_and_the_record(report):
    names = sorted(p.stem for p in (report / "data").glob("*.parquet"))
    assert names == [
        "dim_column",
        "dim_date",
        "dim_kind",
        "dim_run",
        "fact_drift_change",
    ]
    assert (report / "drift.bim").is_file() and (report / "report.json").is_file()
    assert table_of(report, "fact_drift_change").column_names == [
        "run_id",
        "column_id",
        "kind_id",
        "size",
        "threshold",
    ]


def test_a_run_is_a_profile_compared_with_the_one_before_it(report, history):
    runs = rows_of(report, "dim_run")
    assert [r["run_id"] for r in runs] == [1, 2, 3]
    assert [r["run_label"] for r in runs] == [p.stem for p in history[1:]]
    assert [r["baseline_label"] for r in runs] == [p.stem for p in history[:-1]]
    assert [r["run_date"] for r in runs] == [date(2026, 3, 6), date(2026, 3, 11), date(2026, 3, 13)]
    assert [r["date_key"] for r in runs] == [20260306, 20260311, 20260313]


def test_every_planted_change_is_in_fact_drift_change(report):
    facts = rows_of(report, "fact_drift_change")
    columns = {r["column_id"]: r["column_key"] for r in rows_of(report, "dim_column")}
    kinds = {r["kind_id"]: r["kind"] for r in rows_of(report, "dim_kind")}
    found = {(f["run_id"], columns[f["column_id"]], kinds[f["kind_id"]]) for f in facts}
    planted = 0
    for run_id, (a, b) in enumerate(zip(DAYS, DAYS[1:], strict=False), 1):
        for e in PLAN.expected_changes(a, b):
            planted += 1
            assert any((run_id, e["column"], k) in found for k in e["kinds"]), (run_id, e)
    assert planted >= 5  # the history plants null_rate, new_category, distribution, add and type


def test_a_change_has_a_size_and_the_threshold_it_passed(report):
    facts = rows_of(report, "fact_drift_change")
    kinds = {r["kind_id"]: r["kind"] for r in rows_of(report, "dim_kind")}
    by_kind = {}
    for f in facts:
        by_kind.setdefault(kinds[f["kind_id"]], []).append(f)
    for f in facts:
        assert 0.0 <= f["size"] <= 1.0
    assert {f["threshold"] for f in by_kind["null_rate_change"]} == {0.05}
    # a change with no number to pass has no threshold
    assert {f["threshold"] for f in by_kind["column_added"]} == {None}
    assert {f["threshold"] for f in by_kind["dtype_change"]} == {None}
    assert {f["threshold"] for f in by_kind["mean_shift"]} == {0.5}


def test_the_facts_are_the_changes_shape_diff_reports(report, history):
    expected = 0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for a, b in zip(history, history[1:], strict=False):
            expected += len(shape.diff(shape.load(a), shape.load(b)).changes)
    assert table_of(report, "fact_drift_change").num_rows == expected


def test_the_dimensions(report):
    kinds = rows_of(report, "dim_kind")
    assert [k["kind"] for k in kinds] == sorted(k["kind"] for k in kinds)
    assert {k["kind"]: k["severity"] for k in kinds}["dtype_change"] == "high"
    assert {k["kind"]: k["threshold_key"] for k in kinds}["null_rate_change"] == "null_rate"
    columns = {c["column_key"] for c in rows_of(report, "dim_column")}
    assert {"note", "status", "total", "channel", "vip"} <= columns
    dates = rows_of(report, "dim_date")
    assert dates[0]["date"] == date(2026, 3, 6) and dates[-1]["date"] == date(2026, 3, 13)
    assert len(dates) == 8  # every day between the first and the last run
    first = dates[0]
    assert (first["date_key"], first["year"], first["month"], first["month_name"]) == (
        20260306,
        2026,
        3,
        "March",
    )
    assert first["day"] == 6 and first["day_of_week"] == "Friday" and first["quarter"] == 1


def test_the_ids_are_dense_and_every_fact_has_its_dimension_rows(report):
    ids = {
        name: {r[key] for r in rows_of(report, name)}
        for name, key in (
            ("dim_run", "run_id"),
            ("dim_column", "column_id"),
            ("dim_kind", "kind_id"),
        )
    }
    for name, values in ids.items():
        assert values == set(range(1, len(values) + 1)), name
    for f in rows_of(report, "fact_drift_change"):
        assert f["run_id"] in ids["dim_run"]
        assert f["column_id"] in ids["dim_column"]
        assert f["kind_id"] in ids["dim_kind"]


def test_the_share_of_runs_with_a_change_can_be_read_from_the_tables(report):
    facts = table_of(report, "fact_drift_change")
    runs = table_of(report, "dim_run")
    with_change = len(set(facts["run_id"].to_pylist()))
    assert with_change == 3 and runs.num_rows == 3
    # a quiet pair of profiles makes a run with no change in it: the share is under 1
    assert pc.count_distinct(facts["run_id"]).as_py() / runs.num_rows == 1.0


# ---- the model ---------------------------------------------------------------------------------


def test_the_model_has_the_tables_relationships_and_measures(report):
    model = json.loads((report / "drift.bim").read_text(encoding="utf-8"))
    assert model["compatibilityLevel"] == 1604 and model["name"] == "ShapeDrift"
    tables = {t["name"]: t for t in model["model"]["tables"]}
    assert set(tables) == {"dim_column", "dim_date", "dim_kind", "dim_run", "fact_drift_change"}
    rels = {
        (r["fromTable"], r["fromColumn"], r["toTable"], r["toColumn"])
        for r in model["model"]["relationships"]
    }
    assert rels == {
        ("fact_drift_change", "run_id", "dim_run", "run_id"),
        ("fact_drift_change", "column_id", "dim_column", "column_id"),
        ("fact_drift_change", "kind_id", "dim_kind", "kind_id"),
        ("dim_run", "date_key", "dim_date", "date_key"),
    }
    measures = {m["name"]: m["expression"] for m in tables["fact_drift_change"]["measures"]}
    assert set(measures) == {
        "Changes",
        "Runs",
        "Changes per Run",
        "Runs with Change",
        "Share of Runs with Change",
        "Columns with Change",
        "Kinds with Change",
        "Share of Changes by Kind",
        "Share of Changes by Column",
        "Average Change Size",
        "Largest Change Size",
    }
    assert measures["Changes"] == "COUNTROWS('fact_drift_change')"
    assert measures["Share of Runs with Change"] == (
        "DIVIDE('fact_drift_change'[Runs with Change], 'fact_drift_change'[Runs])"
    )
    for name in ("dim_run", "dim_column", "dim_kind", "dim_date"):
        assert "measures" not in tables[name]


def test_every_name_a_measure_uses_exists_in_the_model(report):
    model = json.loads((report / "drift.bim").read_text(encoding="utf-8"))
    tables = {t["name"]: t for t in model["model"]["tables"]}
    names = {
        t["name"]: {c["name"] for c in t["columns"]} | {m["name"] for m in t.get("measures", [])}
        for t in tables.values()
    }
    for t in tables.values():
        for m in t.get("measures", []):
            for table, column in re.findall(r"'([^']+)'\[([^\]]+)\]", m["expression"]):
                assert table in names, m
                assert column in names[table], (m["name"], table, column)


def test_the_columns_of_the_model_match_the_tables(report):
    model = json.loads((report / "drift.bim").read_text(encoding="utf-8"))
    for t in model["model"]["tables"]:
        assert [c["name"] for c in t["columns"]] == table_of(report, t["name"]).column_names


# ---- the record --------------------------------------------------------------------------------


def test_report_json_records_the_inputs_thresholds_and_row_counts(report, history):
    doc = report_json(report)
    assert doc["format"] == "shape-drift-report"
    assert doc["version"] == 1 and isinstance(doc["version"], int)
    assert doc["source"] == {"kind": "files", "registry": None, "since": None}
    assert [i["path"] for i in doc["inputs"]] == [str(p) for p in history]
    assert [i["sha256"] for i in doc["inputs"]] == [sha(p) for p in history]
    assert [i["date"] for i in doc["inputs"]] == [
        "2026-03-01",
        "2026-03-06",
        "2026-03-11",
        "2026-03-13",
    ]
    assert doc["thresholds"]["thresholds"]["null_rate"] == 0.05
    assert doc["thresholds"]["ignore"] == [] and doc["thresholds"]["only"] == []
    assert doc["format_of_tables"] == "parquet"
    assert doc["tables"] == {
        name: table_of(report, name).num_rows
        for name in ("dim_column", "dim_date", "dim_kind", "dim_run", "fact_drift_change")
    }
    assert doc["runs"] == 3 and doc["changes"] == doc["tables"]["fact_drift_change"]


def test_the_same_inputs_give_byte_identical_files(run, history, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for out in (a, b):
        code, _, err = run("publish-report", *map(str, history), "-o", str(out))
        assert (code, err) == (0, "")
    for name in ("drift.bim", "report.json"):
        assert sha(a / name) == sha(b / name), name
    for table in ("dim_column", "dim_date", "dim_kind", "dim_run", "fact_drift_change"):
        assert table_of(a, table).equals(table_of(b, table)), table


def test_the_fabric_group_has_the_command_too(run, history, tmp_path):
    code, _, err = run("fabric", "publish-report", *map(str, history), "-o", str(tmp_path / "o"))
    assert (code, err) == (0, "")


def test_csv_tables_hold_the_same_rows(run, history, report, tmp_path):
    out = tmp_path / "csv"
    code, _, err = run("publish-report", *map(str, history), "-o", str(out), "--format", "csv")
    assert (code, err) == (0, "")
    assert report_json(out)["format_of_tables"] == "csv"
    assert not list((out / "data").glob("*.parquet"))
    for name in ("dim_column", "dim_date", "dim_kind", "dim_run", "fact_drift_change"):
        with open(out / "data" / f"{name}.csv", newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        expected = table_of(report, name)
        assert rows[0] == expected.column_names
        assert len(rows) - 1 == expected.num_rows
    with open(out / "data" / "fact_drift_change.csv", newline="", encoding="utf-8") as fh:
        facts = list(csv.DictReader(fh))
    nulls = [f for f in facts if f["threshold"] == ""]
    assert nulls and all(f["run_id"] for f in facts)
    assert sha(out / "drift.bim") == sha(report / "drift.bim")


# ---- the thresholds and the policy of `shape diff` ---------------------------------------------


def changes_of(directory: Path) -> set[tuple[int, str, str]]:
    columns = {r["column_id"]: r["column_key"] for r in rows_of(directory, "dim_column")}
    kinds = {r["kind_id"]: r["kind"] for r in rows_of(directory, "dim_kind")}
    return {
        (f["run_id"], columns[f["column_id"]], kinds[f["kind_id"]])
        for f in rows_of(directory, "fact_drift_change")
    }


def test_a_threshold_changes_what_is_reported_and_is_recorded(run, history, report, tmp_path):
    out = tmp_path / "strict"
    code, _, err = run(
        "publish-report", *map(str, history), "-o", str(out), "--threshold", "null_rate=0.9"
    )
    assert (code, err) == (0, "")
    assert not {c for c in changes_of(out) if c[2] == "null_rate_change"}
    assert {c for c in changes_of(report) if c[2] == "null_rate_change"}
    assert report_json(out)["thresholds"]["thresholds"]["null_rate"] == 0.9


def test_the_flag_forms_of_the_thresholds_work(run, history, tmp_path):
    out = tmp_path / "flags"
    code, _, err = run(
        "publish-report",
        *map(str, history),
        "-o",
        str(out),
        "--null-rate",
        "0.04",
        "--column-threshold",
        "total:mean_shift_std=0.1",
    )
    assert (code, err) == (0, "")
    doc = report_json(out)
    assert doc["thresholds"]["thresholds"]["null_rate"] == 0.04
    assert doc["thresholds"]["columns"] == {"total": {"mean_shift_std": 0.1}}
    kinds = {r["kind_id"]: r["kind"] for r in rows_of(out, "dim_kind")}
    columns = {r["column_id"]: r["column_key"] for r in rows_of(out, "dim_column")}
    seen = {
        (columns[f["column_id"]], kinds[f["kind_id"]]): f["threshold"]
        for f in rows_of(out, "fact_drift_change")
    }
    assert seen[("note", "null_rate_change")] == 0.04
    assert seen[("total", "mean_shift")] == 0.1  # the column's own threshold, not the default


def test_ignore_only_and_policy_select_columns(run, history, report, tmp_path):
    base = changes_of(report)
    assert any(c[1] == "note" for c in base) and any(c[1] == "total" for c in base)
    out = tmp_path / "ign"
    assert run("publish-report", *map(str, history), "-o", str(out), "--ignore", "note")[0] == 0
    assert not {c for c in changes_of(out) if c[1] == "note"}
    assert {c for c in changes_of(out) if c[1] == "total"}
    out = tmp_path / "only"
    assert run("publish-report", *map(str, history), "-o", str(out), "--only", "note")[0] == 0
    assert {c[1] for c in changes_of(out)} == {"note"}
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"thresholds": {"null_rate": 0.9}, "ignore": ["vip"]}))
    out = tmp_path / "pol"
    assert (
        run("publish-report", *map(str, history), "-o", str(out), "--policy", str(policy))[0] == 0
    )
    got = changes_of(out)
    assert not {c for c in got if c[1] == "vip" or c[2] == "null_rate_change"}
    assert report_json(out)["thresholds"]["ignore"] == ["vip"]


def test_a_bad_threshold_exits_2(run, history, tmp_path):
    for bad in (["--threshold", "nonesuch=1"], ["--threshold", "null_rate"], ["--null-rate", "-1"]):
        code, _, err = run("publish-report", *map(str, history), "-o", str(tmp_path / "o"), *bad)
        assert code == 2, (bad, err)
    code, _, err = run(
        "publish-report", *map(str, history), "-o", str(tmp_path / "o"), "--policy", "missing.json"
    )
    assert code == 2


def test_every_kind_the_engine_reports_has_a_threshold_rule():
    from shape_fabric import drift_report

    from shape.drift.engine import DEFAULT_THRESHOLDS, KIND_SEVERITY

    for kind in KIND_SEVERITY:
        assert kind in drift_report.THRESHOLD_KEYS or kind in drift_report.NO_THRESHOLD, kind
    for kind, keys in drift_report.THRESHOLD_KEYS.items():
        assert kind in KIND_SEVERITY
        assert set(keys) <= set(DEFAULT_THRESHOLDS), kind
    assert not set(drift_report.NO_THRESHOLD) & set(drift_report.THRESHOLD_KEYS)


def test_a_profile_export_is_read_like_a_shape_file(run, history, report, tmp_path):
    exports = []
    for n, path in enumerate(history):
        out = tmp_path / f"orders-2026-03-{n + 1:02d}.json"
        assert main(["profile", "export", str(path), "-o", str(out)]) == 0
        exports.append(str(out))
    out = tmp_path / "o"
    code, _, err = run("publish-report", *exports, "-o", str(out))
    assert (code, err) == (0, "")
    assert (
        table_of(out, "fact_drift_change").num_rows
        == table_of(report, "fact_drift_change").num_rows
    )


def test_every_kind_of_change_is_in_dim_kind_whether_or_not_it_happened(report):
    from shape.drift.engine import KIND_SEVERITY

    kinds = {r["kind"]: r for r in rows_of(report, "dim_kind")}
    assert set(kinds) >= set(KIND_SEVERITY)
    assert kinds["table_added"]["threshold_key"] is None
    assert (
        kinds["cardinality_change"]["threshold_key"]
        == "cardinality_ratio_max|cardinality_ratio_min"
    )
    occurred = {f["kind_id"] for f in rows_of(report, "fact_drift_change")}
    assert len(occurred) < len(kinds)


def test_a_table_level_change_has_the_table_for_its_column_and_a_ratio_threshold_by_direction(
    run, tmp_path
):
    paths = []
    for n, rows in enumerate((3000, 9000, 3000)):  # the table triples, then shrinks back
        table = PLAN.generate_day(SCHEMA, 0, row_counts={"orders": rows})["orders"]
        path = tmp_path / f"orders-2026-04-0{n + 1}.shape"
        shape.save(shape.profile(table, name="orders"), str(path), capture="full")  # W1-11
        paths.append(str(path))
    out = tmp_path / "o"
    code, _, err = run("publish-report", *paths, "-o", str(out))
    assert (code, err) == (0, "")
    kinds = {r["kind_id"]: r["kind"] for r in rows_of(out, "dim_kind")}
    columns = {r["column_id"]: r for r in rows_of(out, "dim_column")}
    rows = [
        f for f in rows_of(out, "fact_drift_change") if kinds[f["kind_id"]] == "row_count_change"
    ]
    assert [(f["run_id"], f["threshold"]) for f in rows] == [(1, 2.0), (2, 0.5)]
    table_level = columns[rows[0]["column_id"]]
    assert table_level["column_key"] == "(table)" and table_level["column_name"] is None


def test_the_docs_list_the_measures_the_model_has(report):
    doc = (Path(__file__).resolve().parents[3] / "docs" / "DRIFT_REPORT.md").read_text(
        encoding="utf-8"
    )
    named = set(re.findall(r"^\| `([^`]+)` \| `[A-Z]+\(", doc, flags=re.M))
    model = json.loads((report / "drift.bim").read_text(encoding="utf-8"))
    fact = next(t for t in model["model"]["tables"] if t["name"] == "fact_drift_change")
    assert named == {m["name"] for m in fact["measures"]}
    for _table, column in re.findall(r"'(fact_drift_change)'\[([a-z_]+)\]", doc):
        assert column in {c["name"] for c in fact["columns"]}
    for name in ("dim_column", "dim_date", "dim_kind", "dim_run", "fact_drift_change"):
        assert f"`{name}`" in doc


# ---- history in a registry ---------------------------------------------------------------------


@pytest.fixture
def registry(history, tmp_path) -> Path:
    from shape.registry import LocalRegistry

    root = tmp_path / "reg"
    reg = LocalRegistry(root)
    dates = ["2026-03-01", "2026-03-06", "2026-03-11", None]  # the last has no business date
    for path, when in zip(history, dates, strict=True):
        meta = {"profile_form": "raw"}
        if when:
            meta["business_date"] = when
        reg.commit("orders", path.read_bytes(), meta, allow_raw=True)
    return root


def test_a_registry_name_is_a_history(run, registry, history, report, tmp_path):
    out = tmp_path / "r"
    code, o, err = run(
        "publish-report", "--registry", "orders", "--registry-root", str(registry), "-o", str(out)
    )
    assert (code, err) == (0, ""), o
    doc = report_json(out)
    assert doc["source"] == {"kind": "registry", "registry": "orders", "since": None}
    assert [i["content_id"] for i in doc["inputs"]] == [
        hashlib.sha256(p.read_bytes()).hexdigest() for p in history
    ]
    runs = rows_of(out, "dim_run")
    assert [r["run_date"] for r in runs][:2] == [date(2026, 3, 6), date(2026, 3, 11)]
    assert runs[2]["run_date"] is not None  # no business date: the commit date
    assert [r["run_label"] for r in runs] == [i["content_id"][:12] for i in doc["inputs"][1:]]
    assert runs[0]["profile"] == doc["inputs"][1]["content_id"]
    kinds = {r["kind_id"]: r["kind"] for r in rows_of(out, "dim_kind")}
    columns = {r["column_id"]: r["column_key"] for r in rows_of(out, "dim_column")}
    got = {
        (f["run_id"], columns[f["column_id"]], kinds[f["kind_id"]])
        for f in rows_of(out, "fact_drift_change")
    }
    assert got == changes_of(report)


def test_since_keeps_the_runs_from_that_date_and_the_profile_before_them(run, registry, tmp_path):
    out = tmp_path / "since"
    code, o, err = run(
        "publish-report",
        "--registry",
        "orders",
        "--registry-root",
        str(registry),
        "--since",
        "2026-03-11",
        "-o",
        str(out),
    )
    assert (code, err) == (0, ""), o
    doc = report_json(out)
    assert doc["source"]["since"] == "2026-03-11"
    assert len(doc["inputs"]) == 3  # the 03-06 profile is the baseline of the first run
    runs = rows_of(out, "dim_run")
    assert [r["run_date"] for r in runs][0] == date(2026, 3, 11)
    assert len(runs) == 2


def test_since_after_every_commit_leaves_fewer_than_two_profiles(run, registry, tmp_path):
    code, _, err = run(
        "publish-report",
        "--registry",
        "orders",
        "--registry-root",
        str(registry),
        "--since",
        "2999-01-01",
        "-o",
        str(tmp_path / "o"),
    )
    assert code == 2 and "two" in err


def test_a_safe_profile_in_the_registry_is_refused(run, history, tmp_path):
    from shape.registry import LocalRegistry

    safe = tmp_path / "safe.json"
    assert main(["profile", "safe", str(history[0]), "-o", str(safe)]) == 0
    reg = LocalRegistry(tmp_path / "reg")
    reg.commit("orders", safe.read_bytes(), {})
    reg.commit("orders", safe.read_bytes(), {})
    code, _, err = run(
        "publish-report",
        "--registry",
        "orders",
        "--registry-root",
        str(tmp_path / "reg"),
        "-o",
        "o",
    )
    assert code == 2 and "safe" in err and "--allow-raw" in err


def test_a_safe_profile_file_is_refused(run, history, tmp_path):
    safe = tmp_path / "safe-2026-03-01.json"
    assert main(["profile", "safe", str(history[0]), "-o", str(safe)]) == 0
    code, _, err = run("publish-report", str(history[0]), str(safe), "-o", "o")
    assert code == 2 and "safe" in err and "--allow-raw" in err and str(safe) in err


@pytest.mark.parametrize(
    "argv",
    [
        ["--registry", "orders"],  # no root
        ["--registry", "nonesuch", "--registry-root", "{reg}"],
        ["--registry", "orders", "--registry-root", "{nowhere}"],
        ["--registry", "orders", "--registry-root", "{reg}", "{profile}"],  # both sources
        ["--registry", "orders", "--registry-root", "{reg}", "--since", "yesterday"],
        ["--registry", "orders", "--registry-root", "{reg}", "--since", "2026-13-40"],
        ["{profile}", "--since", "2026-03-01"],  # --since belongs to --registry
        ["{profile}", "--registry-root", "{reg}"],
    ],
)
def test_registry_options_that_do_not_fit_exit_2(run, registry, history, tmp_path, argv):
    argv = [a.format(reg=registry, nowhere=tmp_path / "nowhere", profile=history[0]) for a in argv]
    code, _, err = run("publish-report", *argv, "-o", str(tmp_path / "o"))
    assert code == 2, err
    assert not (tmp_path / "nowhere").exists()


def test_the_registry_is_not_created_by_a_report(run, tmp_path):
    code, _, _ = run(
        "publish-report", "--registry", "x", "--registry-root", str(tmp_path / "none"), "-o", "o"
    )
    assert code == 2 and not (tmp_path / "none").exists()


# ---- inputs that are wrong ---------------------------------------------------------------------


def test_fewer_than_two_profiles_exits_2(run, history, tmp_path):
    code, _, err = run("publish-report", str(history[0]), "-o", str(tmp_path / "o"))
    assert code == 2 and "two" in err
    code, _, err = run("publish-report", "-o", str(tmp_path / "o"))
    assert code == 2 and "two" in err
    assert not (tmp_path / "o").exists()


def test_a_missing_or_wrong_file_exits_2(run, history, tmp_path):
    code, _, err = run("publish-report", str(history[0]), "missing.shape", "-o", "o")
    assert code == 2 and "missing.shape" in err
    junk = tmp_path / "junk.shape"
    junk.write_text("not a profile")
    code, _, err = run("publish-report", str(history[0]), str(junk), "-o", "o")
    assert code == 2 and "junk.shape" in err


def test_a_bad_format_exits_2(history):
    assert main(["publish-report", *map(str, history), "-o", "o", "--format", "xlsx"]) == 2


def test_the_output_folder_is_required(history):
    assert main(["publish-report", *map(str, history)]) == 2


# ---- quiet history, undated files, dataset profiles --------------------------------------------


def test_two_profiles_of_the_same_quiet_feed_make_one_run_with_no_changes(run, tmp_path):
    quiet = DriftPlan([], start="2026-03-01", days=3)
    paths = []
    for day in (0, 1):
        table = quiet.generate_day(SCHEMA, day, row_counts={"orders": ROWS})["orders"]
        path = tmp_path / f"q{day}.shape"
        shape.save(shape.profile(table, name="orders"), str(path), capture="full")  # W1-11
        paths.append(str(path))
    out = tmp_path / "o"
    code, _, err = run("publish-report", *paths, "-o", str(out))
    assert (code, err) == (0, "")
    assert table_of(out, "fact_drift_change").num_rows == 0
    assert table_of(out, "dim_run").num_rows == 1
    runs = rows_of(out, "dim_run")
    assert runs[0]["run_date"] is None and runs[0]["date_key"] is None  # no date in the names
    assert table_of(out, "dim_date").num_rows == 0
    assert report_json(out)["tables"]["fact_drift_change"] == 0
    assert report_json(out)["inputs"][0]["date"] is None


def test_a_profile_of_several_tables_names_columns_by_table(run, tmp_path):
    from shape.generation.engine import Engine

    doc = json.loads(json.dumps(HISTORY["schema"]))
    result = Engine(GenSchema.from_dict(doc), row_counts={"orders": 500}).generate()
    first = shape.profile({"orders": result["orders"], "orders_copy": result["orders"]}, name="d")
    plan = DriftPlan(
        [{"kind": "null_rate", "table": "orders", "column": "note", "start": 1, "to": 0.6}],
        start="2026-03-01",
        days=3,
    )
    later = plan.generate_day(GenSchema.from_dict(doc), 2, row_counts={"orders": 500})["orders"]
    second = shape.profile({"orders": later, "orders_copy": result["orders"]}, name="d")
    paths = []
    for n, prof in enumerate((first, second)):
        path = tmp_path / f"set{n}.shape"
        shape.save(prof, str(path), capture="full")  # W1-11: the report reads full profiles
        paths.append(str(path))
    out = tmp_path / "o"
    code, _, err = run("publish-report", *paths, "-o", str(out))
    assert (code, err) == (0, ""), err
    columns = {r["column_key"]: r for r in rows_of(out, "dim_column")}
    assert "orders.note" in columns and columns["orders.note"]["table_name"] == "orders"
    assert columns["orders.note"]["column_name"] == "note"
    kinds = {r["kind_id"]: r["kind"] for r in rows_of(out, "dim_kind")}
    changed = {
        (columns_id_to_key(out)[f["column_id"]], kinds[f["kind_id"]])
        for f in rows_of(out, "fact_drift_change")
    }
    assert ("orders.note", "null_rate_change") in changed
    assert not {c for c in changed if c[0].startswith("orders_copy.")}


def columns_id_to_key(directory: Path) -> dict[int, str]:
    return {r["column_id"]: r["column_key"] for r in rows_of(directory, "dim_column")}


# ---- the persisted format: report.json ---------------------------------------------------------

CORPUS = Path(__file__).parent / "data" / "drift_report_v1.json"


def test_the_v1_corpus_still_reads(tmp_path):
    from shape_fabric import drift_report

    doc = drift_report.load_report(CORPUS)
    assert doc["format"] == "shape-drift-report" and doc["version"] == 1
    assert doc["runs"] == len(doc["inputs"]) - 1
    assert set(doc["tables"]) == {
        "dim_column",
        "dim_date",
        "dim_kind",
        "dim_run",
        "fact_drift_change",
    }


def test_a_report_this_version_writes_has_the_corpus_keys(report):
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    assert list(report_json(report)) == list(corpus)
    assert set(report_json(report)["thresholds"]) == set(corpus["thresholds"])


def test_a_newer_or_foreign_report_is_refused(tmp_path):
    from shape_fabric import drift_report
    from shape_fabric.known_answer import KnownAnswerError

    good = json.loads(CORPUS.read_text(encoding="utf-8"))
    cases = {
        "newer.json": (dict(good, version=2), "version 2"),
        "string.json": (dict(good, version="1"), "version"),
        "zero.json": (dict(good, version=0), "version"),
        "other.json": (dict(good, format="shape-dax-answers"), "shape-drift-report"),
        "list.json": ([], "shape-drift-report"),
    }
    for name, (doc, needle) in cases.items():
        path = tmp_path / name
        path.write_text(json.dumps(doc), encoding="utf-8")
        with pytest.raises(KnownAnswerError, match=needle):
            drift_report.load_report(path)
    broken = tmp_path / "broken.json"
    broken.write_text("{nope")
    with pytest.raises(KnownAnswerError, match="broken.json"):
        drift_report.load_report(broken)
