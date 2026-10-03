"""The comparisons of the parity harness (``benchmarks/vs_spindle/bridge_1to1/compare.py``) and the
vector matcher must be able to fail: a result compared with itself agrees, and each mutation is
flagged. (The harness runs the same functions against the baseline's bridge.)"""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest
import vectors_lib as lib

PATH = (
    Path(__file__).resolve().parents[2] / "benchmarks" / "vs_spindle" / "bridge_1to1" / "compare.py"
)
spec = importlib.util.spec_from_file_location("bridge_compare", PATH)
assert spec and spec.loader
compare = importlib.util.module_from_spec(spec)
sys.modules["bridge_compare"] = compare
spec.loader.exec_module(compare)


@pytest.fixture
def results(api):
    return {
        "describe": api.ok("describe", domain="retail"),
        "dry_run": api.ok("dry_run", domain="retail", scale="small"),
        "profile_info": api.ok("profile_info", domain="retail"),
        "preview": api.ok("preview", domain="retail", rows=5, seed=1),
        "generate": api.ok("generate", domain="retail", scale="small"),
        "validate": {
            "valid": True,
            "table_count": 9,
            "relationship_count": 8,
            "errors": [],
            "warnings": [],
        },
    }


def mutated(doc, edit):
    other = copy.deepcopy(doc)
    edit(other)
    return other


def test_a_result_agrees_with_itself(results):
    assert compare.describe(results["describe"], results["describe"]) == []
    assert compare.dry_run(results["dry_run"], results["dry_run"]) == []
    assert compare.profile_info(results["profile_info"], results["profile_info"]) == []
    assert compare.preview(results["preview"], results["preview"]) == []
    assert compare.generate_summary(results["generate"], results["generate"]) == []
    assert compare.validate(results["validate"], results["validate"]) == []


@pytest.mark.parametrize(
    "name, function, edit",
    [
        (
            "describe",
            "describe",
            lambda d: d["tables"]["order"]["columns"][1].update(type="binary"),
        ),
        ("describe", "describe", lambda d: d["tables"]["order"]["columns"].reverse()),
        ("describe", "describe", lambda d: d["relationships"].pop()),
        ("describe", "describe", lambda d: d["business_rules"][0].update(rule="x")),
        ("describe", "describe", lambda d: d["scales"]["small"].update(customer=1)),
        ("describe", "describe", lambda d: d["generation_order"].reverse()),
        ("describe", "describe", lambda d: d["tables"].pop("store")),
        ("describe", "describe", lambda d: d["tables"]["order"].update(primary_key=["x"])),
        ("dry_run", "dry_run", lambda d: d["planned_rows"].update(order=1)),
        ("dry_run", "dry_run", lambda d: d.update(total_rows=1)),
        ("preview", "preview", lambda d: d["tables"]["customer"]["columns"].reverse()),
        (
            "preview",
            "preview",
            lambda d: d["tables"]["customer"]["data"][0].update(customer_id="x"),
        ),
        ("preview", "preview", lambda d: d["tables"]["customer"].update(total_rows=1)),
        ("preview", "preview", lambda d: d["tables"].pop("store")),
        ("generate", "generate_summary", lambda d: d["tables"]["order"].update(rows=1)),
        ("generate", "generate_summary", lambda d: d.update(integrity_pass=False)),
        ("validate", "validate", lambda d: d.update(table_count=1)),
        ("validate", "validate", lambda d: d.update(valid=False)),
        ("validate", "validate", lambda d: d["errors"].append({"location": "x"})),
        ("profile_info", "profile_info", lambda d: d["ratios"].update(address_per_customer=3.0)),
        (
            "profile_info",
            "profile_info",
            lambda d: d["distributions"]["customer.gender"].update(M=0.9),
        ),
        ("profile_info", "profile_info", lambda d: d["distributions"].pop("order.status")),
        ("profile_info", "profile_info", lambda d: d.update(profile="other")),
    ],
)
def test_each_difference_is_flagged(results, name, function, edit):
    base = results[name]
    problems = getattr(compare, function)(base, mutated(base, edit))
    assert problems, f"{function} did not flag the change"


def test_profile_info_may_report_more_than_the_baseline_but_not_less(results):
    base = results["profile_info"]
    more = mutated(
        base,
        lambda d: (
            d["distributions"].update({"extra.column": {"a": 1}}),
            d["distribution_keys"].append("extra.column"),
            d["distribution_keys"].sort(),
        ),
    )
    assert compare.profile_info(base, more) == []
    assert compare.profile_info(more, base)


def test_profile_info_aliases_and_skips_are_the_only_exceptions(results):
    base = results["profile_info"]
    renamed = mutated(
        base,
        lambda d: d["distributions"].update({"old.name": d["distributions"].pop("order.status")}),
    )
    assert compare.profile_info(renamed, base)
    assert compare.profile_info(renamed, base, aliases={"old.name": "order.status"}) == []
    changed = mutated(base, lambda d: d["distributions"]["order.status"].update(completed=0.1))
    assert compare.profile_info(base, changed)
    assert compare.profile_info(base, changed, skip=("order.status",)) == []


def test_lifecycle_phases_are_compared_by_name_not_by_position():
    listed = {"phases": [{"name": "a", "weight": 1}, {"name": "b", "weight": 2}]}
    assert compare._phases(listed) == compare._phases(
        {"phases": [{"name": "b", "weight": 2}, {"name": "a", "weight": 1}]}
    )
    assert compare._phases(listed) != compare._phases(
        {"phases": [{"name": "a", "weight": 2}, {"name": "b", "weight": 1}]}
    )


def test_the_stream_status_comparison():
    base = {
        "stream_id": "x",
        "chunks_written": 2,
        "rows_written": 40,
        "running": False,
        "error": None,
    }
    shape = {**base, "rows_written": 360, "status": "succeeded"}
    assert compare.stream_status(base, shape) == []
    assert compare.stream_status(base, {k: v for k, v in shape.items() if k != "running"})
    assert compare.stream_status(base, {**shape, "chunks_written": 1})
    assert compare.stream_status(base, {**shape, "running": True})


def test_the_scale_generate_comparison():
    base = {
        "domain": "d",
        "scale": "small",
        "scale_mode": "local_mp",
        "sinks_written": {"parquet": "ok"},
    }
    shape = {**base, "rows_generated": 10, "elapsed_seconds": 1, "throughput_rows_per_sec": 10}
    assert compare.scale_generate(base, shape, 10) == []
    assert compare.scale_generate(base, {**shape, "rows_generated": 11}, 10)
    assert compare.scale_generate(base, {**shape, "sinks_written": {"memory": "ok"}}, 10)
    assert compare.scale_generate(
        base, {k: v for k, v in shape.items() if k != "elapsed_seconds"}, 10
    )


# ---- the vector matcher ---------------------------------------------------------------------


def test_the_vector_matcher_requires_every_expected_key_and_value():
    assert lib.matches({"a": 1}, {"a": 1, "b": 2}) == []  # a minor version may add a field
    assert lib.matches({"a": 1, "b": 2}, {"a": 1})  # ... never remove one
    assert lib.matches({"a": 1}, {"a": 2})
    assert lib.matches({"a": [1, 2]}, {"a": [1]}) and lib.matches({"a": [1]}, {"a": [1, 2]})
    assert lib.matches({"a": lib.ANY}, {"a": "anything"}) == [] and lib.matches({"a": lib.ANY}, {})
    assert lib.matches({"a": {"b": 1}}, {"a": 5})
    assert (
        lib.matches({"a": None}, {"a": 0}) and lib.matches({"a": False}, {"a": 0}) == []
    )  # 0 == False in JSON


def test_the_vector_abstraction_keeps_what_is_deterministic():
    doc = {"job_id": "job-abcdef123456", "path": "/work/x.csv", "elapsed_seconds": 1.5, "n": 3}
    assert lib.abstract(doc, "/work") == {
        "job_id": lib.ANY,
        "path": "${DIR}/x.csv",
        "elapsed_seconds": lib.ANY,
        "n": 3,
    }
    assert lib.abstract({"job_id": "job-000000000001"}, "/work") == {"job_id": "job-000000000001"}
