"""W7-05 item 5: drift between two share-safe versions of a registry name, and the metrics a safe
form withholds under ``not_measured``."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
import data_1_2 as data  # noqa: E402

import shape  # noqa: E402
from shape.cli.main import main  # noqa: E402
from shape.registry import LocalRegistry  # noqa: E402
from shape.registry.drift import (  # noqa: E402
    SafeDriftError,
    diff_safe,
    diff_versions,
    is_safe_profile,
    not_measured,
)


def ends(root: Path) -> tuple[LocalRegistry, str, str]:
    reg = LocalRegistry(root)
    log = reg.log("orders")
    return reg, log[0]["content_id"], log[-1]["content_id"]


def safe_doc(day: int, **kw: Any) -> dict[str, Any]:
    doc = json.loads(data.safe_bytes(data.feed_profile(day, **kw)))
    assert is_safe_profile(doc)
    return doc


def kinds(drift: dict[str, Any]) -> dict[tuple[Any, str], dict[str, Any]]:
    return {(c["column"], c["kind"]): c for c in drift["changes"]}


@pytest.fixture(scope="module")
def planted(tmp_path_factory):
    """The same planted null-rate change in a raw history and in its safe form."""
    base = tmp_path_factory.mktemp("planted")
    raw = data.make_registry(base / "raw", days=8, form="raw", null_from=4)
    safe = data.make_registry(base / "safe", days=8, form="safe", null_from=4)
    return raw, safe


def test_a_planted_null_rate_change_is_reported_with_the_kind_and_severity_of_shape_diff(planted):
    raw_root, safe_root = planted
    raw_reg, a, b = ends(raw_root)
    safe_reg, c, d = ends(safe_root)
    from_raw = diff_versions(raw_reg, "orders", a, b)["drift"]
    from_safe = diff_versions(safe_reg, "orders", c, d)["drift"]
    wanted = ("note", "null_rate_change")
    assert wanted in kinds(from_raw) and wanted in kinds(from_safe)
    for key in ("kind", "severity", "column"):
        assert kinds(from_safe)[wanted][key] == kinds(from_raw)[wanted][key]
    assert kinds(from_safe)[wanted]["severity"] == "medium"
    assert kinds(from_safe)[wanted]["score"] == pytest.approx(
        kinds(from_raw)[wanted]["score"], abs=0.1
    )  # the safe form rounds the rates; the size is the same to that precision
    assert from_safe["drifted"] is True and "not_measured" in from_safe
    assert "not_measured" not in from_raw  # two raw profiles give today's result


def test_two_raw_profiles_give_the_result_of_today(planted):
    raw_reg, a, b = ends(planted[0])
    out = diff_versions(raw_reg, "orders", a, b)
    assert set(out) == {"name", "from", "to", "same", "drift"}
    assert set(out["drift"]) == {"drifted", "changes"}


def test_the_range_of_a_column_is_not_measured_in_a_safe_form(tmp_path):
    root = data.make_registry(tmp_path / "wide", days=6, form="safe", wide_from=3)
    reg, a, b = ends(root)
    drift = diff_versions(reg, "orders", a, b)["drift"]
    assert drift["drifted"] is True
    metrics = {(n["column"], n["metric"]) for n in drift["not_measured"]}
    assert ("amount", "range") in metrics
    assert ("amount", "outlier_rate") in metrics
    assert not [k for k in kinds(drift) if k[1] == "range_change"]  # never reported, never silent
    assert ("amount", "mean_shift") in kinds(drift)  # what both forms hold is still compared
    for entry in drift["not_measured"]:
        assert set(entry) == {"table", "column", "metric", "reason"} and entry["reason"]


def test_a_stable_history_has_no_drift_and_still_lists_what_it_cannot_measure(tmp_path):
    root = data.make_registry(tmp_path / "calm", days=5, form="safe")
    reg, a, b = ends(root)
    drift = diff_versions(reg, "orders", a, b)["drift"]
    assert drift["drifted"] is False and drift["changes"] == []
    assert ("amount", "range") in {(n["column"], n["metric"]) for n in drift["not_measured"]}


def test_the_changes_are_those_of_shape_diff_on_the_same_profiles(tmp_path):
    """Every metric both forms hold gives the record the engine gives for the full profiles."""
    first, second = data.feed_profile(0), data.feed_profile(5, null_from=0, wide_from=0)
    full = shape.diff(first, second).to_dict()["changes"]
    safe = diff_safe(json.loads(data.safe_bytes(first)), json.loads(data.safe_bytes(second)))[
        "changes"
    ]
    by_full = {(c["column"], c["kind"]): c for c in full}
    shared = [c for c in safe if (c["column"], c["kind"]) in by_full]
    assert {c["kind"] for c in shared} >= {"null_rate_change", "mean_shift"}
    for c in shared:
        record = by_full[(c["column"], c["kind"])]
        assert (c["severity"], c["kind"], c["column"]) == (
            record["severity"],
            record["kind"],
            record["column"],
        )


def test_thresholds_policy_ignore_and_only_apply_as_in_shape_diff(tmp_path):
    a, b = safe_doc(0), safe_doc(5, null_from=0)
    assert ("note", "null_rate_change") in kinds(diff_safe(a, b))
    tight = diff_safe(a, b, thresholds={"null_rate": 0.9})
    assert ("note", "null_rate_change") not in kinds(tight)
    assert not diff_safe(a, b, ignore_columns=["note"])["changes"]
    only = diff_safe(a, b, only_columns=["status"])
    assert all(c["column"] == "status" for c in only["changes"])
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"drift": {"thresholds": {"null_rate": 0.9}}}))
    assert ("note", "null_rate_change") not in kinds(diff_safe(a, b, policy=str(policy)))
    with pytest.raises(ValueError):
        diff_safe(a, b, thresholds={"no_such_threshold": 1})


def test_an_ignored_column_is_left_out_of_not_measured_too():
    a, b = safe_doc(0), safe_doc(1)
    assert any(n["column"] == "amount" for n in diff_safe(a, b)["not_measured"])
    ignored = diff_safe(a, b, ignore_columns=["amount"])["not_measured"]
    assert not any(n["column"] == "amount" for n in ignored)


def test_a_metric_one_safe_form_does_not_hold_is_not_measured(tmp_path):
    a, b = safe_doc(0), safe_doc(1)
    stripped = copy.deepcopy(b)
    stripped["tables"]["orders"]["columns"]["amount"]["mean"] = None
    stripped["tables"]["orders"]["columns"]["amount"]["quantiles"] = None
    names = {(n["column"], n["metric"]) for n in not_measured(a, stripped)}
    assert {("amount", "mean"), ("amount", "distribution_shift")} <= names
    assert ("amount", "mean") not in {(n["column"], n["metric"]) for n in not_measured(a, b)}
    no_weights = copy.deepcopy(b)
    no_weights["tables"]["orders"]["columns"]["status"]["categorical_weights"] = None
    assert ("status", "categories") in {
        (n["column"], n["metric"]) for n in not_measured(a, no_weights)
    }


def test_a_column_added_or_a_type_change_is_reported_as_structure():
    a, b = safe_doc(0), safe_doc(1)
    more = copy.deepcopy(b)
    more["tables"]["orders"]["columns"]["extra"] = {
        **more["tables"]["orders"]["columns"]["status"],
        "name": "extra",
    }
    assert ("extra", "column_added") in kinds(diff_safe(a, more))
    retyped = copy.deepcopy(b)
    retyped["tables"]["orders"]["columns"]["amount"]["dtype"] = "string"
    drift = diff_safe(a, retyped)
    assert ("amount", "dtype_change") in kinds(drift)
    assert not [n for n in drift["not_measured"] if n["column"] == "amount"]  # reported as a type


def test_a_dataset_of_several_tables_names_the_table(tmp_path):
    a, b = safe_doc(0), safe_doc(5, null_from=0)
    for doc in (a, b):
        doc["tables"] = {
            "orders": doc["tables"]["orders"],
            "again": copy.deepcopy(doc["tables"]["orders"]),
        }
    drift = diff_safe(a, b)
    assert ("orders.note", "null_rate_change") in kinds(drift)
    assert {n["table"] for n in drift["not_measured"]} == {"orders", "again"}
    assert all(n["column"].startswith(f"{n['table']}.") for n in drift["not_measured"])


def test_a_document_that_is_not_a_safe_profile_is_refused():
    good = safe_doc(0)
    for bad in ({}, {"tables": {}}, {"tables": {"t": {}}}, {"redaction_manifest": {}}, []):
        with pytest.raises(SafeDriftError):
            diff_safe(good, bad)  # type: ignore[arg-type]
    empty = copy.deepcopy(good)
    empty["tables"] = {}
    with pytest.raises(SafeDriftError):
        diff_safe(good, empty)
    assert not is_safe_profile({"format": "shape-profile"}) and not is_safe_profile(None)


def test_two_other_documents_keep_the_changed_path_output(tmp_path):
    reg = LocalRegistry(tmp_path / "reg")
    reg.commit("doc", json.dumps({"a": 1, "b": {"c": 2}}))
    reg.commit("doc", json.dumps({"a": 1, "b": {"c": 3}}))
    reg.commit("blob", b"one")
    reg.commit("blob", b"two")
    log = reg.log("doc")
    out = diff_versions(reg, "doc", log[0]["content_id"], log[1]["content_id"])
    assert out["changed"] == {"b.c": {"from": 2, "to": 3}} and "drift" not in out
    log = reg.log("blob")
    assert diff_versions(reg, "blob", log[0]["content_id"], log[1]["content_id"])["changed"] is None


def test_the_command_line_prints_the_drift_with_not_measured(planted, capsys):
    _, safe_root = planted
    reg, a, b = ends(safe_root)
    assert main(["registry", str(safe_root), "diff", "orders", a, b]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == diff_versions(reg, "orders", a, b)
    assert printed["drift"]["drifted"] is True and printed["drift"]["not_measured"]
    assert printed["changed"]  # the paths that differ stay, as before
    assert main(["registry", str(safe_root), "diff", "orders", a, a]) == 0
    same = json.loads(capsys.readouterr().out)
    assert same["same"] is True and "drift" not in same and same["changed"] == {}


def test_a_raw_and_a_safe_version_are_not_compared_as_profiles(tmp_path):
    root = data.make_registry(tmp_path / "mixed", days=6, form="mixed")
    reg = LocalRegistry(root)
    log = reg.log("orders")
    out = diff_versions(reg, "orders", log[0]["content_id"], log[-1]["content_id"])
    assert "drift" not in out and out["changed"] is None  # today's output for such a pair
