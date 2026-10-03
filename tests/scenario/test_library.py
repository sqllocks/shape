"""W5-05 items 2 and 3: the starter scenario library, its answer keys, the formats and suites."""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pyarrow as pa
import pytest

from shape.errors import ShapeError
from shape.generation.drift_plan import DriftPlan
from shape.generation.schema import GenSchema
from shape.scenario.library import (
    LibraryError,
    UnknownScenarioError,
    list_scenarios,
    list_suites,
    load_expect,
    load_scenario,
    load_suite,
    mismatches,
    run_scenario,
    run_suite,
)
from shape.scenario.library import formats as library_formats
from shape.scenario.library.defects import DefectError, apply_defects
from shape.scenario.library.formats import (
    parse_expect,
    parse_index,
    parse_scenario,
    parse_suite,
)
from shape.scenario.library.scale import resolve_scale

pytest.importorskip("shape_domains")

ROOT = library_formats.ROOT
IDS = [e["id"] for e in list_scenarios()]
DRIFT_IDS = [i for i in IDS if load_scenario(i).get("drift")]


@pytest.fixture
def library(tmp_path: Path) -> Path:
    """A copy of the shipped library that a test may damage."""
    dest = tmp_path / "library"
    shutil.copytree(ROOT, dest, ignore=shutil.ignore_patterns("__pycache__", "*.py"))
    return dest


def edit(path: Path, change) -> None:
    doc = json.loads(path.read_text())
    change(doc)
    path.write_text(json.dumps(doc, indent=2))


# ---- the library ----------------------------------------------------------------------------


def test_the_library_has_the_required_scenarios_with_a_paragraph_each():
    assert len(IDS) >= 8
    for needed in (
        "clean_baseline",
        "nulls_injected",
        "duplicate_rows",
        "orphaned_foreign_keys",
        "late_arriving_data",
        "schema_add_column",
        "schema_rename_column",
        "schema_drop_column",
        "schema_retype_column",
    ):
        assert needed in IDS
    for entry in list_scenarios():
        assert entry["description"].strip() and "\n" not in entry["description"]
        assert (ROOT / "scenarios" / entry["id"] / "scenario.json").is_file()
        assert (ROOT / "scenarios" / entry["id"] / "expect.json").is_file()
    assert sorted(p.name for p in (ROOT / "scenarios").iterdir() if p.is_dir()) == sorted(IDS)


@pytest.mark.parametrize("name", IDS)
def test_every_scenario_meets_its_own_answer_key(name):
    result = run_scenario(name)
    assert result.mismatches == [], result.outcome.summary()
    assert result.met and result.outcome.scenario == name


@pytest.mark.parametrize("name", IDS)
def test_every_scenario_also_meets_its_key_at_the_tiny_scale(name):
    result = run_scenario(name, scale="tiny")
    assert result.met, [str(m) for m in result.mismatches]


@pytest.mark.parametrize("name", IDS)
def test_scenarios_and_keys_agree_with_each_other(name):
    spec, key = load_scenario(name), load_expect(name)
    assert spec["id"] == key["scenario"] == name
    assert set(key["gates_fail"]) <= set(spec["gates"])
    assert set(key["defects"]) <= {d["kind"] for d in spec.get("defects", [])}


def test_the_data_scenarios_fail_exactly_the_gates_their_keys_name():
    expected = {
        "clean_baseline": [],
        "nulls_injected": ["null_check"],
        "duplicate_rows": ["uniqueness"],
        "orphaned_foreign_keys": ["referential_integrity"],
        "late_arriving_data": [],
    }
    for name, failing in expected.items():
        outcome = run_scenario(name).outcome
        assert [g for g, ok in outcome.gates.items() if not ok] == failing, name
        assert set(outcome.gates) == set(load_scenario(name)["gates"])


@pytest.mark.parametrize("name", DRIFT_IDS)
def test_a_drift_answer_key_agrees_with_the_plans_own_expected_changes(name):
    """The key is written by hand; the plan computes the same changes from its events."""
    spec, key = load_scenario(name), load_expect(name)
    plan = DriftPlan.from_dict(spec["drift"]["plan"])
    tables = {e.table for e in plan.events}

    def qualified(column: str) -> str:  # a plan over one table names columns without it
        return column if len(tables) > 1 else f"{next(iter(tables))}.{column}"

    for window in key["drift"]:
        a, b = window["between"]
        derived = set()
        for rec in plan.expected_changes(a, b):
            derived.add((qualified(rec["column"]), rec["kinds"][0]))
            if "rename" in rec:
                derived.add((qualified(rec["rename"]["to"]), rec["rename"]["added_kinds"][0]))
        written = {(c["column"], c["kinds"][0]) for c in window["changes"]}
        assert derived == written, (name, window["between"])


def test_the_schema_evolution_scenario_covers_add_rename_drop_and_retype():
    plan = load_scenario("schema_evolution_schedule")["drift"]["plan"]
    assert [e["kind"] for e in plan["events"]] == [
        "add_column",
        "rename_column",
        "drop_column",
        "type_change",
    ]
    days = [e["start"] for e in plan["events"]]
    assert days == sorted(days) and len(set(days)) == 4


def test_a_rerun_gives_the_same_outcome_and_the_seed_can_be_overridden():
    a = run_scenario("nulls_injected").outcome.to_dict()
    b = run_scenario("nulls_injected").outcome.to_dict()
    a.pop("elapsed_seconds"), b.pop("elapsed_seconds")
    assert a == b
    other = run_scenario("nulls_injected", seed=7).outcome
    assert other.seed == 7 and other.gates["null_check"] is False


def test_the_output_option_writes_the_tables_under_the_scenario_name(tmp_path):
    result = run_scenario("clean_baseline", scale="tiny", output=tmp_path)
    names = {Path(f).name for f in result.outcome.files}
    assert {"customer.parquet", "order.parquet", "return.parquet"} <= names
    assert all(Path(f).parent == tmp_path / "clean_baseline" for f in result.outcome.files)
    drift = run_scenario("schema_add_column", scale="tiny", output=tmp_path)
    assert (tmp_path / "schema_add_column" / "day_10" / "order.parquet").is_file()
    assert drift.outcome.files


def test_unknown_scale_or_scenario_is_refused_with_the_choices():
    with pytest.raises(UnknownScenarioError, match="clean_baseline"):
        run_scenario("no_such_scenario")
    with pytest.raises(ShapeError, match="fabric_demo.*tiny"):
        run_scenario("clean_baseline", scale="gigantic")


# ---- a wrong answer key ---------------------------------------------------------------------


def test_a_key_that_wants_a_gate_to_fail_that_passes_is_a_mismatch(library):
    edit(
        library / "scenarios/clean_baseline/expect.json",
        lambda d: d.update(gates_fail=["uniqueness"]),
    )
    result = run_scenario("clean_baseline", root=library)
    assert not result.met
    (m,) = result.mismatches
    assert m.expected == "gate uniqueness fails" and m.observed == "gate uniqueness passed"


def test_a_key_that_forgets_a_failing_gate_is_a_mismatch(library):
    edit(library / "scenarios/nulls_injected/expect.json", lambda d: d.update(gates_fail=[]))
    (m,) = run_scenario("nulls_injected", root=library).mismatches
    assert m.expected == "gate null_check passes"
    assert "gate null_check failed (customer.last_name has nulls)" == m.observed


def test_a_key_that_wants_more_planted_rows_than_there_are_is_a_mismatch(library):
    edit(
        library / "scenarios/nulls_injected/expect.json",
        lambda d: d.update(defects={"inject_nulls": {"min": 10**6}}),
    )
    (m,) = run_scenario("nulls_injected", root=library).mismatches
    assert "at least 1000000 rows of inject_nulls" in m.expected and "80 rows" in m.observed


def test_a_drift_key_that_names_a_change_the_diff_does_not_report_is_a_mismatch(library):
    def wrong(doc):
        doc["drift"][0]["changes"][0]["column"] = "order.channels"

    edit(library / "scenarios/schema_add_column/expect.json", wrong)
    found = run_scenario("schema_add_column", root=library).mismatches
    assert any("order.channels reported as column_added" in m.expected for m in found)
    # the real change is now one the key does not cover
    assert any(m.expected.startswith("no column_added on order.channel") for m in found)


def test_an_unexpected_structural_change_is_a_mismatch(library):
    edit(
        library / "scenarios/schema_rename_column/expect.json",
        lambda d: d["drift"][0]["changes"].pop(),  # forget the added column
    )
    (m,) = run_scenario("schema_rename_column", root=library).mismatches
    assert m.expected == "no column_added on order.order_status between days 0 and 10"


def test_the_mismatch_helper_reports_a_window_that_did_not_run():
    key = {"gates_fail": [], "defects": {}, "drift": [{"between": [0, 99], "changes": []}]}
    from shape.scenario.library import Outcome

    (m,) = mismatches(key, Outcome("x", "retail", "small", 1))
    assert "days 0 and 99" in m.expected and "no such window ran" in m.observed


def test_a_key_that_lists_a_gate_the_scenario_does_not_check_is_refused(library):
    edit(
        library / "scenarios/clean_baseline/scenario.json",
        lambda d: d.update(gates=["row_count"]),
    )
    edit(
        library / "scenarios/clean_baseline/expect.json",
        lambda d: d.update(gates_fail=["null_check"]),
    )
    with pytest.raises(LibraryError, match="gates the scenario does not check: null_check"):
        run_scenario("clean_baseline", root=library)


# ---- the three persisted formats ------------------------------------------------------------

V1_SCENARIO = {
    "format": "shape-scenario",
    "version": 1,
    "id": "x",
    "domain": "retail",
    "scale": "small",
    "seed": 3,
    "gates": ["row_count"],
    "defects": [{"kind": "inject_nulls", "table": "customer", "column": "last_name"}],
}
V1_EXPECT = {
    "format": "shape-scenario-expect",
    "version": 1,
    "scenario": "x",
    "gates_fail": ["row_count"],
    "defects": {"inject_nulls": {"min": 1}},
    "drift": [{"between": [0, 5], "changes": [{"column": "t.c", "kinds": ["column_added"]}]}],
}
V1_INDEX = {
    "format": "shape-scenario-library",
    "version": 1,
    "scenarios": [{"id": "x", "domain": "retail", "description": "A paragraph."}],
}
V1_SUITE = {"format": "shape-suite", "version": 1, "name": "s", "scenarios": ["x"]}


def test_version_one_files_of_every_format_still_load():
    """The compatibility test: these literals are frozen version 1 documents."""
    assert parse_scenario(copy.deepcopy(V1_SCENARIO), "s")["seed"] == 3
    assert parse_expect(copy.deepcopy(V1_EXPECT), "e")["gates_fail"] == ["row_count"]
    assert parse_index(copy.deepcopy(V1_INDEX), "i")[0]["id"] == "x"
    assert parse_suite(copy.deepcopy(V1_SUITE), "u")["scenarios"] == ["x"]


@pytest.mark.parametrize(
    ("fmt", "files"),
    [
        ("shape-scenario", sorted((ROOT / "scenarios").glob("*/scenario.json"))),
        ("shape-scenario-expect", sorted((ROOT / "scenarios").glob("*/expect.json"))),
        ("shape-scenario-library", [ROOT / "index.json"]),
        ("shape-suite", sorted((ROOT / "suites").glob("*.json"))),
    ],
)
def test_every_shipped_file_declares_its_format_and_an_integer_version(fmt, files):
    assert files
    for path in files:
        doc = json.loads(path.read_text())
        assert doc["format"] == fmt and doc["version"] == 1, path
        assert type(doc["version"]) is int


@pytest.mark.parametrize(
    ("parse", "doc"),
    [
        (parse_scenario, V1_SCENARIO),
        (parse_expect, V1_EXPECT),
        (parse_index, V1_INDEX),
        (parse_suite, V1_SUITE),
    ],
)
def test_every_format_refuses_a_newer_version_a_missing_one_a_wrong_format_and_extra_keys(
    parse, doc
):
    newer = {**doc, "version": 2}
    with pytest.raises(LibraryError, match="version 2.*newer Shape"):
        parse(newer, "the file")
    for bad in (None, "1", True, 0, 1.0):
        with pytest.raises(LibraryError, match="integer 'version'"):
            parse({**doc, "version": bad}, "the file")
    without = {k: v for k, v in doc.items() if k != "version"}
    with pytest.raises(LibraryError, match="integer 'version'"):
        parse(without, "the file")
    with pytest.raises(LibraryError, match="is not a shape"):
        parse({**doc, "format": "something-else"}, "the file")
    with pytest.raises(LibraryError, match="unknown keys: surprise"):
        parse({**doc, "surprise": 1}, "the file")
    with pytest.raises(LibraryError, match="JSON object"):
        parse([doc], "the file")


@pytest.mark.parametrize(
    ("parse", "doc", "change", "message"),
    [
        (parse_scenario, V1_SCENARIO, {"id": ""}, "needs a 'id'"),
        (parse_scenario, V1_SCENARIO, {"seed": True}, "'seed' must be an integer"),
        (parse_scenario, V1_SCENARIO, {"gates": "row_count"}, "list of names"),
        (parse_scenario, V1_SCENARIO, {"defects": {}}, "'defects' must be a list"),
        (parse_scenario, {**V1_SCENARIO, "gates": [], "defects": []}, {}, "checks nothing"),
        (parse_scenario, V1_SCENARIO, {"drift": {"plan": {}, "compare": [[0, 1]]}}, "no gates"),
        (
            parse_scenario,
            {k: v for k, v in V1_SCENARIO.items() if k not in ("gates", "defects")},
            {"drift": {"plan": {}, "compare": [[0]]}},
            "pair of day numbers",
        ),
        (parse_expect, V1_EXPECT, {"scenario": 3}, "'scenario'"),
        (parse_expect, V1_EXPECT, {"defects": {"k": {"max": 1}}}, "maps a defect kind"),
        (parse_expect, V1_EXPECT, {"drift": [{"between": [0, 1]}]}, "'between' and 'changes'"),
        (parse_expect, V1_EXPECT, {"drift": "x"}, "list of windows"),
        (parse_index, V1_INDEX, {"scenarios": []}, "non-empty"),
        (
            parse_index,
            V1_INDEX,
            {"scenarios": [V1_INDEX["scenarios"][0]] * 2},
            "listed twice",
        ),
        (parse_index, V1_INDEX, {"scenarios": [{"id": "x"}]}, "an entry has"),
        (parse_suite, V1_SUITE, {"scenarios": []}, "non-empty"),
        (parse_suite, V1_SUITE, {"scenarios": ["a", 3]}, "list of names"),
    ],
)
def test_malformed_documents_are_refused_with_a_message(parse, doc, change, message):
    with pytest.raises(LibraryError, match=message):
        parse({**doc, **change}, "the file")


def test_an_expect_file_needs_gates_fail_even_when_empty():
    doc = {k: v for k, v in V1_EXPECT.items() if k != "gates_fail"}
    with pytest.raises(LibraryError, match="needs 'gates_fail'"):
        parse_expect(doc, "the key")


def test_a_scenario_listed_under_another_name_or_a_key_for_another_scenario_is_refused(library):
    edit(library / "scenarios/clean_baseline/scenario.json", lambda d: d.update(id="other"))
    with pytest.raises(LibraryError, match="is the scenario 'other', listed as 'clean_baseline'"):
        load_scenario("clean_baseline", library)
    edit(library / "scenarios/clean_baseline/expect.json", lambda d: d.update(scenario="other"))
    with pytest.raises(LibraryError, match="belongs to scenario 'other'"):
        load_expect("clean_baseline", library)


def test_a_file_that_is_not_json_or_is_missing_names_the_file(library):
    (library / "scenarios/clean_baseline/scenario.json").write_text("{nope")
    with pytest.raises(LibraryError, match="not valid JSON"):
        load_scenario("clean_baseline", library)
    (library / "scenarios/clean_baseline/expect.json").unlink()
    with pytest.raises(LibraryError, match="not found"):
        load_expect("clean_baseline", library)


# ---- defects --------------------------------------------------------------------------------

DOC = {
    "schema_version": 1,
    "model": {"name": "t", "seed": 1, "schema_mode": "3nf"},
    "tables": {
        "t": {
            "name": "t",
            "primary_key": ["id"],
            "columns": {
                "id": {"name": "id", "type": "integer", "generator": {"strategy": "sequence"}},
                "parent": {
                    "name": "parent",
                    "type": "integer",
                    "generator": {"strategy": "sequence"},
                },
                "label": {"name": "label", "type": "string", "generator": {"strategy": "sequence"}},
                "at": {"name": "at", "type": "timestamp", "generator": {"strategy": "sequence"}},
            },
        },
        "pair": {
            "name": "pair",
            "primary_key": ["a", "b"],
            "columns": {
                "a": {"name": "a", "type": "integer", "generator": {"strategy": "sequence"}},
                "b": {"name": "b", "type": "integer", "generator": {"strategy": "sequence"}},
            },
        },
    },
    "generation": {"scale": "small", "scales": {"small": {"t": 10}}},
}
SCHEMA = GenSchema.from_dict(DOC)


def small_table(n: int = 20) -> pa.Table:
    import datetime as dt

    return pa.table(
        {
            "id": pa.array(range(n), pa.int64()),
            "parent": pa.array(range(n), pa.int64()),
            "label": [f"l{i}" for i in range(n)],
            "at": pa.array(
                [dt.datetime(2026, 1, 1) + dt.timedelta(days=i) for i in range(n)],
                pa.timestamp("us"),
            ),
        }
    )


def plant(defect, table=None, seed=5):
    tables = {"t": table if table is not None else small_table()}
    return apply_defects(tables, [{"table": "t", **defect}], SCHEMA, seed)


def test_nulls_hit_the_asked_share_and_at_least_one_row():
    out, changed = plant({"kind": "inject_nulls", "column": "label", "fraction": 0.25})
    assert changed == {"inject_nulls": 5} and out["t"]["label"].null_count == 5
    out, changed = plant({"kind": "inject_nulls", "column": "label", "fraction": 0.001})
    assert changed == {"inject_nulls": 1}  # a tiny fraction still plants one row
    out, changed = plant({"kind": "inject_nulls", "column": "label", "fraction": 1})
    assert out["t"]["label"].null_count == 20 and changed == {"inject_nulls": 20}
    assert out["t"].schema.field("label").nullable


@pytest.mark.parametrize("fraction", [0, -0.1, 1.01])
def test_a_fraction_outside_zero_to_one_is_refused(fraction):
    with pytest.raises(DefectError, match="'fraction' must be above 0"):
        plant({"kind": "inject_nulls", "column": "label", "fraction": fraction})


def test_defects_are_deterministic_for_a_seed_and_differ_between_seeds():
    one = plant({"kind": "inject_nulls", "column": "label", "fraction": 0.3}, seed=1)[0]["t"]
    again = plant({"kind": "inject_nulls", "column": "label", "fraction": 0.3}, seed=1)[0]["t"]
    other = plant({"kind": "inject_nulls", "column": "label", "fraction": 0.3}, seed=2)[0]["t"]
    assert one.equals(again) and not one.equals(other)


def test_duplicate_keys_repeat_keys_of_rows_that_stay():
    out, changed = plant({"kind": "duplicate_keys", "fraction": 0.2})
    ids = out["t"]["id"].to_pylist()
    assert changed == {"duplicate_keys": 4} and len(set(ids)) < 20
    assert set(ids) <= set(range(20))
    with pytest.raises(DefectError, match="single-column key"):
        apply_defects(
            {"pair": pa.table({"a": [1, 2], "b": [1, 2]})},
            [{"kind": "duplicate_keys", "table": "pair"}],
            SCHEMA,
            1,
        )
    with pytest.raises(DefectError, match="at least two rows"):
        plant({"kind": "duplicate_keys"}, table=small_table(1))
    with pytest.raises(DefectError, match="no row to copy"):
        plant({"kind": "duplicate_keys", "fraction": 1})


def test_orphan_keys_use_values_no_parent_has_and_keep_the_type():
    out, changed = plant({"kind": "orphan_keys", "column": "parent", "fraction": 0.1})
    values = out["t"]["parent"].to_pylist()
    assert changed == {"orphan_keys": 2} and sum(v >= 900_000_000 for v in values) == 2
    assert out["t"]["parent"].type == pa.int64()
    with pytest.raises(DefectError, match="not an integer"):
        plant({"kind": "orphan_keys", "column": "label"})


def test_late_arrivals_move_rows_back_and_need_a_date_column():
    base = small_table()
    out, changed = plant(
        {"kind": "late_arrivals", "column": "at", "fraction": 0.5, "days": 30}, base
    )
    before, after = base["at"].to_pylist(), out["t"]["at"].to_pylist()
    moved = [(b - a).days for a, b in zip(after, before, strict=True) if a != b]
    assert changed == {"late_arrivals": 10} and moved == [30] * 10
    assert out["t"]["at"].type == base["at"].type
    with pytest.raises(DefectError, match="not a date"):
        plant({"kind": "late_arrivals", "column": "label"})
    with pytest.raises(DefectError, match="'days' must be at least 1"):
        plant({"kind": "late_arrivals", "column": "at", "days": 0})


def test_a_malformed_defect_names_what_is_wrong():
    for defect, message in (
        ({"kind": "melt", "table": "t"}, "needs a 'kind'"),
        ({"kind": "inject_nulls", "table": "nope", "column": "x"}, "no table 'nope'"),
        ({"kind": "inject_nulls", "table": "t"}, "needs a 'column'"),
        ({"kind": "inject_nulls", "table": "t", "column": "zzz"}, "no column t.zzz"),
        ("not a mapping", "needs a 'kind'"),
    ):
        with pytest.raises(DefectError, match=message):
            apply_defects({"t": small_table()}, [defect], SCHEMA, 1)


# ---- scales ---------------------------------------------------------------------------------


def test_scale_names_resolve_to_presets_or_tiny():
    assert resolve_scale(SCHEMA, None) == (None, None)
    assert resolve_scale(SCHEMA, "small") == ("small", None)
    preset, rows = resolve_scale(SCHEMA, "tiny")
    assert preset is None and rows == {"t": 100, "pair": 100}
    with pytest.raises(ShapeError, match="small, tiny"):
        resolve_scale(SCHEMA, "huge")
    bare = copy.deepcopy(DOC)
    bare["generation"] = {"scale": "x", "scales": {}}
    with pytest.raises(ShapeError, match="are: tiny"):
        resolve_scale(GenSchema.from_dict(bare), "huge")


# ---- suites ---------------------------------------------------------------------------------


def test_the_built_in_suites_are_smoke_schema_evolution_and_failure_modes():
    # W6-03 adds the failure-modes suite beside the two of W5-05
    assert list_suites() == ["failure-modes", "schema-evolution", "smoke"]
    smoke = load_suite("smoke")["scenarios"]
    assert smoke[0] == "clean_baseline" and set(smoke) <= set(IDS)
    evolution = load_suite("schema-evolution")["scenarios"]
    assert {"schema_add_column", "schema_rename_column", "schema_drop_column"} <= set(evolution)
    assert {"schema_retype_column", "schema_evolution_schedule"} <= set(evolution)


def test_the_smoke_suite_meets_its_keys_well_inside_a_minute():
    result = run_suite("smoke", scale="small")
    assert result.met and len(result.results) == len(load_suite("smoke")["scenarios"])
    assert sum(r.outcome.elapsed_seconds for r in result.results) < 60
    assert result.to_dict()["met"] is True


def test_the_schema_evolution_suite_meets_its_keys():
    assert run_suite("schema-evolution", scale="tiny").met


def test_a_suite_file_runs_and_a_seed_applies_to_every_scenario(tmp_path):
    suite = tmp_path / "mine.json"
    suite.write_text(
        json.dumps({**V1_SUITE, "name": "mine", "scenarios": ["clean_baseline", "nulls_injected"]})
    )
    result = run_suite(suite, scale="tiny", seed=9)
    assert result.name == "mine" and result.met
    assert {r.outcome.seed for r in result.results} == {9}


def test_an_unknown_scenario_or_suite_runs_nothing(tmp_path):
    suite = tmp_path / "bad.json"
    suite.write_text(json.dumps({**V1_SUITE, "scenarios": ["clean_baseline", "ghost"]}))
    out = tmp_path / "out"
    with pytest.raises(LibraryError, match="unknown scenarios: ghost"):
        run_suite(suite, output=out)
    assert not out.exists()  # the first scenario was not run
    with pytest.raises(LibraryError, match="no suite 'nope'.*schema-evolution, smoke"):
        run_suite("nope")


def test_a_suite_with_a_wrong_key_is_not_met(library):
    edit(library / "scenarios/nulls_injected/expect.json", lambda d: d.update(gates_fail=[]))
    result = run_suite("smoke", scale="tiny", root=library)
    assert not result.met
    bad = [r for r in result.results if not r.met]
    assert [r.outcome.scenario for r in bad] == ["nulls_injected"]
    assert result.to_dict()["scenarios"][1]["mismatches"][0]["expected"] == "gate null_check passes"
