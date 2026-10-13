"""FRZ-01 T-21 a-f, fixed seeds and measured negative controls."""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

BENCH = Path(__file__).resolve().parents[2] / "benchmarks" / "regression"


def verifier():
    p = BENCH / "domain_1to1" / "verify.py"
    spec = importlib.util.spec_from_file_location("frz_domain_verify", p)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def sample():
    n = 10000
    frames = {
        "parent": pd.DataFrame(
            {
                "id": np.arange(n),
                "amount": np.linspace(1.0, 1.01, n),
                "category": ["a", "b"] * (n // 2),
            }
        ),
        "child": pd.DataFrame({"child_id": np.arange(n), "parent_id": np.arange(n)}),
    }
    types = {
        t: {c: ("double" if c == "amount" else "string" if c == "category" else "int64") for c in f}
        for t, f in frames.items()
    }
    raw = {
        "domain": "stub",
        "tables": {
            t: {
                "columns": {
                    c: {"generator": {"strategy": "sequence"}, "nullable": True} for c in f
                },
                "primary_key": ["id" if t == "parent" else "child_id"],
            }
            for t, f in frames.items()
        },
        "relationships": [
            {
                "parent": "parent",
                "child": "child",
                "parent_columns": ["id"],
                "child_columns": ["parent_id"],
            }
        ],
        "business_rules": [],
    }
    pools = SimpleNamespace(
        pool_for=lambda *args: None,
        effective=lambda t, c, g: g,
        component_fn=lambda *args: None,
        dataset=lambda *args: [],
    )
    return raw, (frames, types, list(frames)), pools


def test_equal_recorded_candidate_and_retired_clauses():
    v = verifier()
    raw, a, pools = sample()
    r = v.compare_runs(raw, a, copy.deepcopy(a), [copy.deepcopy(a)] * 4, pools)
    assert not r["flagged"]
    assert r["clauses"] == list("abcdef")
    assert "business_rules" not in r and "refengine_fidelity_comparator" not in r


@pytest.mark.parametrize(
    "control,clause",
    [
        ("numeric", "c"),
        ("category", "d"),
        ("nulls", "b"),
        ("rows", "a"),
        ("foreign_key", "f"),
        ("order", "a"),
        ("type", "a"),
    ],
)
def test_domain_controls_are_measurably_outside_bounds(control, clause):
    v = verifier()
    raw, a, pools = sample()
    b = copy.deepcopy(a)
    frames, types, order = b
    if control == "numeric":
        frames["parent"]["amount"] *= 1.05
    if control == "category":
        frames["parent"]["category"] = frames["parent"]["category"].replace({"a": "changed"})
    if control == "nulls":
        frames["parent"].loc[:999, "amount"] = np.nan
    if control == "rows":
        frames["child"] = frames["child"].iloc[:-200]
    if control == "foreign_key":
        frames["child"].loc[:99, "parent_id"] = 10001
    if control == "order":
        frames["parent"] = frames["parent"][["amount", "id", "category"]]
    if control == "type":
        types["parent"]["id"] = "double"
    r = v.compare_runs(raw, a, b, [copy.deepcopy(a)] * 4, pools)
    assert any(f"({clause})" in x for x in r["flagged"]), r


def test_seed_set_is_fixed_and_recording_is_fail_closed(tmp_path):
    v = verifier()
    assert v.REF_SEED == 42 and v.BASELINE_SEEDS == (43, 44, 45, 46) and v.IMPL_SEED == 1042
    with pytest.raises(ValueError, match="seed"):
        v.validate_seed("baseline", 47)
    with pytest.raises(ValueError, match="seed"):
        v.validate_seed("shape", 42)


def test_parquet_bytes_are_informational_but_content_is_authenticated(monkeypatch, tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    v = verifier()
    pq.write_table(pa.table({"id": [1, 2]}), tmp_path / "t.parquet")
    record = {"seeds": {"42": v.run_digests(tmp_path)}}
    record["seeds"]["42"]["tables"]["t"]["parquet_sha256"] = "0" * 64
    record["seeds"]["42"]["tables"]["t"]["parquet_bytes"] += 1
    monkeypatch.setattr(v.generate, "out_dir", lambda *a: tmp_path)
    v.authenticate_run("stub", "small", 42, record)
    record["seeds"]["42"]["tables"]["t"]["dataset_id"] = "changed"
    with pytest.raises(v.RecordingError, match="seed42 table t"):
        v.authenticate_run("stub", "small", 42, record)


def test_negative_control_command_catches_all_seven(monkeypatch, tmp_path, capsys):
    v = verifier()
    raw, run, pools = sample()
    monkeypatch.setattr(v, "load_schema_json", lambda *a: raw)
    monkeypatch.setattr(v, "load", lambda *a, **kw: {})
    monkeypatch.setattr(v, "sha256", lambda *a: "a" * 64)
    monkeypatch.setattr(v, "authenticate_run", lambda *a: None)
    monkeypatch.setattr(v, "load_run", lambda *a: copy.deepcopy(run))
    monkeypatch.setattr(v, "Pools", lambda *a: pools)
    monkeypatch.setattr(v, "manifest", lambda: {"shape_commit": "a" * 40})
    args = ["--no-generate", "--negative-control", "--report", str(tmp_path / "report.json")]
    assert v.main(args) == 0
    assert capsys.readouterr().out.count(": caught") == 7
    monkeypatch.setattr(v, "negative_controls", lambda *a: {"numeric": False})
    assert v.main(args) == 1
    assert "MISSED" in capsys.readouterr().out

    monkeypatch.setattr(v, "negative_controls", lambda *a: {"numeric": True})
    failed_report = {
        **v.compare_runs(raw, run, run, [run] * 4, pools),
        "flagged": ["planted regression"],
    }
    monkeypatch.setattr(v, "compare_runs", lambda *a: failed_report)
    assert v.main(args) == 1


@pytest.mark.parametrize(
    "rows, settings, expected",
    [
        ([{"value": "a"}, {"value": "b"}], {}, {"a", "b"}),
        ([{"name": "a", "value": "wrong"}], {}, {"a"}),
        ([{"name": "wrong", "value": "a"}], {"field": "value"}, {"a"}),
        ([{"name": "a", "value": "wrong"}], {"field": "missing"}, {"a"}),
        ([{"": "wrong", "name": "a"}], {"field": ""}, {"a"}),
    ],
)
def test_reference_pool_uses_baseline_provider_field_selection(rows, settings, expected):
    v = verifier()
    pools = v.Pools.__new__(v.Pools)
    pools._datasets = {(None, "frozen"): rows}
    gen = {"strategy": "reference_data", "dataset": "frozen", **settings}
    assert pools.pool_for(gen) == expected
    candidate = pd.Series(list(expected) * 1000)
    # Every valid frozen value is included; an unrelated value remains invalid.
    B = {"null": 0.0, "tvd": 0.0, "dratio_dev": 0.0}
    positive = v.compare_column(candidate, candidate, B, pools.pool_for(gen), None)
    negative = v.compare_column(
        candidate,
        pd.Series(["not-a-baseline-value"] * len(candidate)),
        B,
        pools.pool_for(gen),
        None,
    )
    assert positive["checks"]["vocab"] is True
    assert negative["checks"]["vocab"] is False


def test_reference_pool_refuses_records_without_a_baseline_value_field():
    v = verifier()
    pools = v.Pools.__new__(v.Pools)
    pools._datasets = {(None, "frozen"): [{"other": "not-a-provider-value"}]}
    with pytest.raises(v.RecordingError, match="frozen: no baseline value field"):
        pools.pool_for({"strategy": "reference_data", "dataset": "frozen"})


def test_column_pool_uses_the_generator_the_release_applies():
    """A fixture's nested reference field is generated as a constant by the release."""
    v = verifier()
    pools = v.Pools.__new__(v.Pools)
    pools._datasets = {(None, "gics_sectors"): [{"sector": "Energy"}]}
    pools.generators = {"industry": {"industry_name": {"strategy": "constant", "value": ""}}}
    declared = {
        "strategy": "reference_data",
        "dataset": "gics_sectors",
        "field": "industries.industry_name",
    }
    raw = {"tables": {"industry": {"columns": {"industry_name": {"generator": declared}}}}}
    # The release's own output (a constant) has no vocabulary to compare against.
    assert v.column_pool(pools, raw, {}, "industry", "industry_name") is None
    # Without a release record the fixture's generator still refuses a field-less dataset.
    pools.generators = {}
    with pytest.raises(v.RecordingError, match="gics_sectors: no baseline value field"):
        v.column_pool(pools, raw, {}, "industry", "industry_name")
