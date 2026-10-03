"""W3-01: ``shape.rules.history`` and ``shape rules backtest``."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

import shape
from shape.generation.drift_plan import DriftPlan
from shape.generation.schema import GenSchema
from shape.registry import LocalRegistry
from shape.rules import backtest
from shape.rules.history import BacktestError

DOC = {
    "schema_version": 1,
    "model": {"name": "feed", "seed": 7, "schema_mode": "3nf"},
    "tables": {
        "orders": {
            "name": "orders",
            "primary_key": ["order_id"],
            "columns": {
                "order_id": {
                    "name": "order_id",
                    "type": "integer",
                    "generator": {"strategy": "sequence"},
                },
                "status": {
                    "name": "status",
                    "type": "string",
                    "generator": {
                        "strategy": "weighted_enum",
                        "values": {"completed": 80, "shipped": 15, "cancelled": 5},
                    },
                },
                "total": {
                    "name": "total",
                    "type": "float",
                    "generator": {
                        "strategy": "distribution",
                        "distribution": "log_normal",
                        "mean": 4.0,
                        "sigma": 0.5,
                    },
                },
                "note": {
                    "name": "note",
                    "type": "string",
                    "nullable": True,
                    "null_rate": 0.02,
                    "generator": {"strategy": "weighted_enum", "values": {"a": 1, "b": 1}},
                },
            },
        }
    },
    "generation": {"scale": "small", "scales": {"small": {"orders": 1500}}},
}
SCHEMA = GenSchema.from_dict(DOC)
EVENTS = [
    {
        "kind": "null_rate",
        "table": "orders",
        "column": "note",
        "start": "2026-03-04",
        "ramp_days": 4,
        "to": 0.4,
    },
    {
        "kind": "new_category",
        "table": "orders",
        "column": "status",
        "start": "2026-03-08",
        "value": "lost",
        "share": 0.1,
    },
]
DAYS = 14
START = dt.date(2026, 3, 1)
ENCODES = {
    "columns": {
        "note": {"max_null_rate": 0.1},
        "status": {"allowed_values": ["completed", "shipped", "cancelled"]},
    }
}
IGNORES = {"columns": {"order_id": {"nullable": False}}}


@pytest.fixture(scope="module")
def feed(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """A registry with one profile a day from a drift plan, and the plan's answer key."""
    root = tmp_path_factory.mktemp("feed")
    plan = DriftPlan(EVENTS, start=START.isoformat(), days=DAYS)
    reg = LocalRegistry(root / "reg")
    tables = []
    for n in range(DAYS):
        day = plan.generate_day(SCHEMA, n, row_counts={"orders": 1500})["orders"]
        tables.append(day)
        path = root / f"day{n}.shape"
        shape.save(shape.profile(day, name="orders", sketches=True), path)
        day_iso = (START + dt.timedelta(days=n)).isoformat()
        reg.commit("orders", path.read_bytes(), {"business_date": day_iso}, allow_raw=True)
    return {"root": root / "reg", "truth": plan.ground_truth(), "tables": tables, "dir": root}


def incidents_from(truth: dict[str, Any], last_day: str) -> dict[str, Any]:
    return {
        "format": "shape-incidents",
        "version": 1,
        "incidents": [
            {"id": e["id"], "from": e["start"], "to": e["end"] or last_day, "note": e["kind"]}
            for e in truth["events"]
        ],
    }


def test_a_contract_that_encodes_the_events_catches_them_and_one_that_does_not_misses_them(
    feed: dict[str, Any],
) -> None:
    last = (START + dt.timedelta(days=DAYS - 1)).isoformat()
    inc = incidents_from(feed["truth"], last)
    hit = backtest(feed["root"], "orders", ENCODES, incidents=inc).to_dict()
    assert [i["status"] for i in hit["incidents"]] == ["caught", "caught"]
    assert hit["incident_summary"] == {"caught": 2, "missed": 0}
    by_id = {i["id"]: i for i in hit["incidents"]}
    assert by_id["e1"]["first_caught_by"].startswith("2026-03-0")
    assert by_id["e2"]["first_caught_by"].startswith("2026-03-08")
    assert hit["alarms_outside_incidents"] == []
    miss = backtest(feed["root"], "orders", IGNORES, incidents=inc)
    assert miss.missed == ["e1", "e2"]
    assert miss.to_dict()["summary"]["fail"] == 0


def test_the_report_lists_every_version_oldest_first_with_the_failed_rules(
    feed: dict[str, Any],
) -> None:
    doc = backtest(feed["root"], "orders", ENCODES).to_dict()
    assert doc["format"] == "shape-backtest-report" and doc["version"] == 1
    days = [e["first_date"] for e in doc["entries"]]
    assert days == sorted(days) and len(days) == DAYS
    assert doc["summary"] == {
        "entries": DAYS,
        "pass": doc["summary"]["pass"],
        "fail": doc["summary"]["fail"],
        "not_measured": 0,
    }
    assert doc["entries"][0]["status"] == "pass" and doc["entries"][0]["failed_rules"] == []
    failing = [e for e in doc["entries"] if e["status"] == "fail"]
    assert failing and all(e["failed_rules"] for e in failing)
    assert "orders.status.allowed_values" in failing[-1]["failed_rules"]
    assert doc["rules"]["orders.note.max_null_rate"]["failed"] > 0


def test_an_alarm_outside_every_incident_is_listed(feed: dict[str, Any]) -> None:
    noisy = {"columns": {"note": {"max_null_rate": 0.001}}}  # the quiet feed holds 2% nulls
    last = (START + dt.timedelta(days=DAYS - 1)).isoformat()
    doc = backtest(
        feed["root"], "orders", noisy, incidents=incidents_from(feed["truth"], last)
    ).to_dict()
    assert len(doc["alarms_outside_incidents"]) >= 3  # the days before the first event


def test_incident_dates_are_inclusive_and_to_is_optional(feed: dict[str, Any]) -> None:
    fail_days = {
        e["first_date"]
        for e in backtest(feed["root"], "orders", ENCODES).entries
        if e["status"] == "fail"
    }
    first = min(fail_days)
    one_day = {
        "format": "shape-incidents",
        "version": 1,
        "incidents": [{"id": "x", "from": first, "note": "n"}],
    }
    assert backtest(feed["root"], "orders", ENCODES, incidents=one_day).missed == []
    before = (dt.date.fromisoformat(first) - dt.timedelta(days=1)).isoformat()
    day_before = {
        "format": "shape-incidents",
        "version": 1,
        "incidents": [{"id": "x", "from": before, "to": before, "note": ""}],
    }
    assert backtest(feed["root"], "orders", ENCODES, incidents=day_before).missed == ["x"]
    ends_on = {
        "format": "shape-incidents",
        "version": 1,
        "incidents": [{"id": "x", "from": before, "to": first, "note": ""}],
    }
    assert backtest(feed["root"], "orders", ENCODES, incidents=ends_on).missed == []


@pytest.mark.parametrize(
    "doc, message",
    [
        ({"version": 1, "incidents": []}, "format"),
        ({"format": "shape-incidents", "incidents": []}, "version"),
        ({"format": "shape-incidents", "version": 9, "incidents": []}, "version 9"),
        ({"format": "shape-incidents", "version": 1}, "incidents"),
        ({"format": "shape-incidents", "version": 1, "incidents": [{"from": "2026-03-01"}]}, "id"),
        ({"format": "shape-incidents", "version": 1, "incidents": [{"id": "a"}]}, "from"),
        (
            {
                "format": "shape-incidents",
                "version": 1,
                "incidents": [{"id": "a", "from": "2026-03-05", "to": "2026-03-01"}],
            },
            "before it starts",
        ),
        (
            {"format": "shape-incidents", "version": 1, "incidents": [{"id": "a", "from": "soon"}]},
            "not a date",
        ),
        (
            {
                "format": "shape-incidents",
                "version": 1,
                "incidents": [{"id": "a", "from": "2026-03-01"}, {"id": "a", "from": "2026-03-02"}],
            },
            "twice",
        ),
        (
            {
                "format": "shape-incidents",
                "version": 1,
                "incidents": [{"id": "a", "from": "2026-03-01", "color": "red"}],
            },
            "unknown keys",
        ),
    ],
)
def test_a_bad_incidents_file_is_an_error(
    feed: dict[str, Any], doc: dict[str, Any], message: str
) -> None:
    with pytest.raises(BacktestError, match=message):
        backtest(feed["root"], "orders", ENCODES, incidents=doc)


def test_since_and_until_limit_the_versions_inclusively(feed: dict[str, Any]) -> None:
    doc = backtest(
        feed["root"], "orders", ENCODES, since="2026-03-05", until="2026-03-07"
    ).to_dict()
    assert [e["first_date"] for e in doc["entries"]] == ["2026-03-05", "2026-03-06", "2026-03-07"]
    assert doc["since"] == "2026-03-05" and doc["until"] == "2026-03-07"
    one = backtest(feed["root"], "orders", ENCODES, since="2026-03-14", until="2026-03-14")
    assert len(one.entries) == 1


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"since": "2027-01-01"}, "no committed version"),
        ({"until": "2025-01-01"}, "no committed version"),
        ({"since": "2026-03-05", "until": "2026-03-01"}, "before --since"),
        ({"since": "yesterday"}, "not a date"),
        ({"window": "year"}, "window"),
    ],
)
def test_unusable_ranges_are_errors(
    feed: dict[str, Any], kwargs: dict[str, Any], message: str
) -> None:
    with pytest.raises(BacktestError, match=message):
        backtest(feed["root"], "orders", ENCODES, **kwargs)


def test_an_unknown_name_and_a_bad_contract_are_errors(
    feed: dict[str, Any], tmp_path: Path
) -> None:
    with pytest.raises(BacktestError, match="nothing is recorded for 'nope'"):
        backtest(feed["root"], "nope", ENCODES)
    with pytest.raises(BacktestError, match="unknown contract keys"):
        backtest(feed["root"], "orders", {"bogus": 1})
    bad = tmp_path / "c.json"
    bad.write_text("{not json")
    with pytest.raises(BacktestError, match="not valid JSON"):
        backtest(feed["root"], "orders", bad)
    with pytest.raises(BacktestError, match="tables"):
        backtest(feed["root"], "orders", {"tables": {"orders": ENCODES}})


def test_versions_run_by_business_date_then_commit_date(tmp_path: Path) -> None:
    reg = LocalRegistry(tmp_path / "r")
    for n, meta in enumerate(
        [{"business_date": "2026-05-02"}, {"business_date": "2026-05-01"}, {}]
    ):
        t = pa.table({"x": list(range(10 + n))})
        path = tmp_path / f"{n}.shape"
        shape.save(shape.profile(t, name="t"), path)
        reg.commit("t", path.read_bytes(), meta, allow_raw=True)
    entries = backtest(reg, "t", {"row_count": {"min": 1}}).entries
    today = dt.datetime.now(dt.UTC).date().isoformat()
    assert [e["first_date"] for e in entries] == ["2026-05-01", "2026-05-02", today] or [
        e["first_date"] for e in entries
    ] == sorted(e["first_date"] for e in entries)
    assert entries[-1]["first_date"] == today  # no business date: the commit date
    assert [e["versions"][0] for e in entries][0] != [e["versions"][0] for e in entries][1]


def _safe_commit(tmp_path: Path, reg: LocalRegistry, table: pa.Table, day: str) -> None:
    from shape.privacy.safe_profile import SafeConfig, to_safe_profile

    prof = shape.profile(table, name="t")
    doc = to_safe_profile(prof, SafeConfig(k=2)).to_json().encode()
    reg.commit("t", doc, {"business_date": day})


def test_a_rule_the_stored_form_cannot_evaluate_is_not_measured_never_a_pass(
    tmp_path: Path,
) -> None:
    reg = LocalRegistry(tmp_path / "r")
    t = pa.table({"id": list(range(100)), "v": [float(i % 7) for i in range(100)]})
    _safe_commit(tmp_path, reg, t, "2026-05-01")
    contract = {
        "columns": {
            "id": {"unique": True, "min": 0, "dtype": "integer"},
            "v": {"max_null_rate": 0.5},
        }
    }
    (entry,) = backtest(reg, "t", contract).entries
    assert "t.id.unique" in entry["not_measured_rules"]
    assert "t.id.min" in entry["not_measured_rules"]
    assert entry["rules"] == {"passed": 2, "failed": 0, "not_measured": 2}
    assert entry["status"] == "not_measured"
    only_measured = backtest(reg, "t", {"columns": {"v": {"max_null_rate": 0.5}}}).entries[0]
    assert only_measured["status"] == "pass"
    failing = backtest(reg, "t", {"columns": {"v": {"max_null_rate": -1}}}).entries[0]
    assert failing["status"] == "fail" and failing["failed_rules"] == ["t.v.max_null_rate"]


def test_an_unreadable_version_is_not_measured_and_does_not_stop_the_replay(tmp_path: Path) -> None:
    reg = LocalRegistry(tmp_path / "r")
    reg.commit("t", b"just some bytes", {"business_date": "2026-05-01"})
    t = pa.table({"id": [1, 2, 3]})
    path = tmp_path / "a.shape"
    shape.save(shape.profile(t, name="t"), path)
    reg.commit("t", path.read_bytes(), {"business_date": "2026-05-02"}, allow_raw=True)
    first, second = backtest(reg, "t", {"row_count": {"min": 1}}).entries
    assert first["status"] == "not_measured" and "not a profile" in first["reason"]
    assert second["status"] == "pass"


EXACT = {
    "row_count": {"min": 1000, "max": 11000},
    "columns": {
        "order_id": {"nullable": False, "dtype": "integer", "min": 0},
        "note": {"max_null_rate": 0.1, "nullable": True},
        "total": {"max": 500},
    },
}


def test_a_week_gives_the_pass_or_fail_of_the_profile_of_its_union(feed: dict[str, Any]) -> None:
    from shape.rules.evaluate import FAIL, evaluate

    doc = backtest(feed["root"], "orders", EXACT, window="week").to_dict()
    assert [e["id"] for e in doc["entries"]] == ["2026-W09", "2026-W10", "2026-W11"]
    assert {e["window"] for e in doc["entries"]} == {"week"}
    assert [len(e["versions"]) for e in doc["entries"]] == [1, 7, 6]
    groups = [feed["tables"][0:1], feed["tables"][1:8], feed["tables"][8:14]]
    for entry, tables in zip(doc["entries"], groups, strict=True):
        union = shape.profile(pa.concat_tables(tables), name="orders")
        want = evaluate(dict(union.tables), False, EXACT)
        assert sorted(o.id for o in want if o.status == FAIL) == sorted(entry["failed_rules"])
        assert entry["not_measured_rules"] == []
    assert {e["status"] for e in doc["entries"]} == {"pass", "fail"}


def test_a_window_leaves_whole_data_statistics_not_measured(feed: dict[str, Any]) -> None:
    contract = {
        "columns": {
            "status": {"allowed_values": ["completed", "shipped", "cancelled"]},
            "note": {"pattern": "x", "max_null_rate": 0.3},
            "order_id": {"unique": True},
        }
    }
    doc = backtest(feed["root"], "orders", contract, window="month").to_dict()
    assert [e["id"] for e in doc["entries"]] == ["2026-03"]
    (entry,) = doc["entries"]
    assert "orders.status.allowed_values" in entry["not_measured_rules"]
    assert "orders.note.pattern" in entry["not_measured_rules"]
    assert "orders.note.max_null_rate" not in entry["not_measured_rules"]
    assert entry["status"] in ("fail", "not_measured")
    assert entry["from"] == "2026-03-01" and entry["to"] == "2026-03-31"


def test_a_window_without_sketches_merges_the_exact_statistics_only(tmp_path: Path) -> None:
    reg = LocalRegistry(tmp_path / "r")
    for n in range(3):
        path = tmp_path / f"{n}.shape"
        shape.save(shape.profile(pa.table({"x": list(range(50))}), name="t"), path)
        reg.commit("t", path.read_bytes(), {"business_date": f"2026-05-0{n + 1}"}, allow_raw=True)
    (entry,) = backtest(
        reg, "t", {"row_count": {"max": 100}, "columns": {"x": {"unique": True}}}, window="month"
    ).entries
    assert entry["failed_rules"] == ["t.row_count.max"]  # 150 rows in the union
    assert "t.x.unique" in entry["not_measured_rules"]


def test_compare_reports_the_versions_on_which_two_contracts_disagree(feed: dict[str, Any]) -> None:
    doc = backtest(feed["root"], "orders", ENCODES, compare=IGNORES).to_dict()
    assert doc["compare"]["disagreements"] > 0
    by_id = {e["id"]: e for e in doc["entries"]}
    for row in doc["compare"]["entries"]:
        assert row["status"] == by_id[row["id"]]["status"] and row["old_status"] == "pass"
    same = backtest(feed["root"], "orders", ENCODES, compare=ENCODES).to_dict()
    assert same["compare"] == {"disagreements": 0, "entries": []}
    assert "compare" not in backtest(feed["root"], "orders", ENCODES).to_dict()


def test_the_report_is_stable_and_json(feed: dict[str, Any]) -> None:
    a = backtest(feed["root"], "orders", ENCODES).to_dict()
    b = backtest(feed["root"], "orders", ENCODES).to_dict()
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_a_dataset_history_runs_with_a_tables_contract(tmp_path: Path) -> None:
    reg = LocalRegistry(tmp_path / "r")
    for n in range(2):
        data = {
            "orders": pa.table({"order_id": list(range(20 + n))}),
            "items": pa.table(
                {"item_id": list(range(30)), "order_id": [i % 20 for i in range(30)]}
            ),
        }
        path = tmp_path / f"{n}.shape"
        shape.save(shape.profile(data), path)
        reg.commit(
            "shop", path.read_bytes(), {"business_date": f"2026-05-0{n + 1}"}, allow_raw=True
        )
    contract = {
        "tables": {"orders": {"row_count": {"min": 21}}, "items": {"row_count": {"max": 100}}}
    }
    first, second = backtest(reg, "shop", contract).entries
    assert first["failed_rules"] == ["orders.row_count.min"] and second["status"] == "pass"
