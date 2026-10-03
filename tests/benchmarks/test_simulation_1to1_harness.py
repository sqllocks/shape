"""P6-04: the simulation parity harness's own rules, without the baseline.

``benchmarks/vs_spindle/simulation_1to1/verify_patterns.py`` compares the simulators with the pinned
baseline. These tests check the comparison itself on tables built here: the same table passes,
a deliberately damaged one fails (the rules' negative control), the seed set cannot be changed,
and every allow-list entry has a probe in a case module.
"""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pyarrow as pa
import pytest

BENCH = Path(__file__).resolve().parents[2] / "benchmarks" / "vs_spindle" / "simulation_1to1"


def _load(*modules: str) -> list[ModuleType]:
    """Import the harness modules from their directory. Other harnesses have modules called
    ``harness``, ``names`` or ``verify`` too, so the names are cleared while these load and the
    other modules put back afterwards (these stay available as ``sim1to1_<name>``)."""
    saved_path = list(sys.path)
    taken = ("harness", "names", "verify_patterns", "paths")
    stale = {m: sys.modules.pop(m) for m in taken if m in sys.modules}
    sys.path[:0] = [str(BENCH.parent), str(BENCH)]
    try:
        loaded = [importlib.import_module(m) for m in modules]
        for m in taken:
            if m in sys.modules:
                sys.modules[f"sim1to1_{m}"] = sys.modules.pop(m)
    finally:
        sys.path[:] = saved_path
        sys.modules.update(stale)
    return loaded


h, names, verify = _load("harness", "names", "verify_patterns")


def table(seed: int, n: int = 3000, bias: float = 0.0) -> pa.Table:
    rng = np.random.default_rng(seed)
    return pa.table(
        {
            "x": pa.array(rng.normal(10 + bias, 2, n)),
            "kind": pa.array(rng.choice(["a", "b", "c"], n, p=[0.5, 0.3, 0.2])),
            "when": pa.array(
                (rng.random(n) * 86400e6).astype("int64").astype("datetime64[us]"),
                pa.timestamp("us"),
            ),
        }
    )


def baseline(**kw: float) -> dict[int, pa.Table]:
    return {s: table(s, **kw) for s in (h.REF_SEED, *h.BASELINE_SEEDS)}


def check(shape: pa.Table, base: dict[int, pa.Table]) -> h.Report:
    rep = h.Report("t")
    h.compare_table(rep, "t", shape, base, h.TableSpec())
    return rep


def test_a_table_from_the_same_process_passes():
    rep = check(table(1042), baseline())
    assert not rep.failed and len(rep.checks) >= 8


@pytest.mark.parametrize(
    "damage",
    [
        lambda t: t.set_column(0, "x", pa.array(np.asarray(t.column("x")) * 1.1)),
        lambda t: t.set_column(
            1,
            "kind",
            pa.array(np.where(np.arange(t.num_rows) % 4 == 0, "b", t.column("kind").to_pylist())),
        ),
        lambda t: t.rename_columns(["x", "kind2", "when"]),
        lambda t: t.slice(0, 2000),
        lambda t: t.set_column(
            0,
            "x",
            pa.array(np.where(np.arange(t.num_rows) % 8 == 0, np.nan, np.asarray(t.column("x")))),
        ),
    ],
    ids=["scaled", "relabelled", "renamed", "short", "nulls"],
)
def test_a_damaged_table_fails(damage):
    assert check(damage(table(1042)), baseline()).failed


def test_a_shifted_distribution_fails_the_ks_rule():
    assert any(c.name.endswith("x:ks") for c in check(table(1042, bias=0.5), baseline()).failed)


def test_the_seed_set_is_fixed():
    assert (h.REF_SEED, h.BASELINE_SEEDS, h.SHAPE_SEED) == (42, (43, 44, 45, 46), 1042)
    with pytest.raises(h.HarnessError, match="fixed"):
        h.baseline_runs("clickstream", {}, None, seeds=(1, 2, 3))


def test_counts_scalars_and_vectors_obey_their_rules():
    rep = h.Report("c")
    h.compare_counts(rep, "n", 1000, [990, 1010, 1000, 995, 1005])
    h.compare_counts(rep, "m", 5, [5, 5, 5, 5, 5], exact=True)
    h.compare_scalar(rep, "s", 0.50, [0.49, 0.51, 0.50, 0.50, 0.52])
    assert not rep.failed
    rep = h.Report("c")
    h.compare_counts(rep, "n", 1500, [990, 1010, 1000, 995, 1005])
    h.compare_counts(rep, "m", 6, [5, 5, 5, 5, 5], exact=True)
    h.compare_scalar(rep, "s", 0.9, [0.49, 0.51, 0.50, 0.50, 0.52])
    assert len(rep.failed) == 3
    rng = np.random.default_rng(0)
    vec = {s: rng.normal(0, 1, 2000) for s in (42, 43, 44, 45, 46)}
    ok, bad = h.Report("v"), h.Report("v")
    h.compare_vector(ok, "v", rng.normal(0, 1, 2000), vec)
    h.compare_vector(bad, "v", rng.normal(0.4, 1, 2000), vec)
    assert not ok.failed and bad.failed


def test_output_mutations_cover_the_kinds_of_damage():
    run = h.Run({"t": table(1)}, {})
    assert set(h.mutations(run)) == {
        "rename a column",
        "scale a numeric column by 1.1",
        "null 10% of a numeric column",
        "relabel a fifth of a category",
        "drop 30% of the rows",
    }


def test_conform_adds_missing_columns_as_nulls():
    like = pa.table({"a": [1], "b": ["x"], "c": [True]})
    out = h.conform(pa.table({"b": ["y", "z"]}), like)
    assert out.column_names == ["a", "b", "c"] and out.column("a").null_count == 2
    with pytest.raises(h.HarnessError):
        h.conform(pa.table({"zzz": [1]}), like)


def test_every_case_module_is_complete_and_every_allow_list_entry_has_a_probe():
    cases = verify.discover()
    assert {"clickstream", "financial", "iot", "operational_log", "pulse"} <= set(cases)
    labels = []
    for module_name in cases.values():
        text = (BENCH / f"{module_name}.py").read_text()
        for attr in (
            "NAME",
            "SIM",
            "def inputs",
            "def configs",
            "def controls",
            "def run_shape",
            "def compare",
        ):
            assert attr in text, (module_name, attr)
        labels += re.findall(r'Report\("(SIM-\d+) ', text)
    assert set(labels) <= set(names.ALLOWED)
    assert {k for k in names.ALLOWED if k.startswith("SIM-")} <= set(labels)


def test_the_baseline_names_are_confined_to_the_harness():
    for path in (BENCH / "names.py").parent.glob("*.py"):
        assert path.is_file()
    assert set(names.MODULES) >= {"clickstream_patterns", "pulse_patterns"}
    assert names.PARAMETERS["FinancialStreamSimulator"]["transactions_df"] == "transactions"


def test_exit_codes(monkeypatch, capsys, tmp_path):
    """0 when everything passes, 1 on a failed check, an undetected control or a failed probe,
    2 when the baseline is missing."""
    ok = h.Report("a")
    ok.add("x", True)
    bad = h.Report("a")
    bad.add("x", False)
    results = {
        "pass": ([ok], [{"control": "c", "detected": True, "failed_checks": ["x"]}], []),
        "failed check": ([bad], [], []),
        "undetected control": (
            [ok],
            [{"control": "c", "detected": False, "failed_checks": []}],
            [],
        ),
        "failed probe": ([ok], [], [_probe(False)]),
    }
    current: dict[str, object] = {}
    monkeypatch.setattr(verify, "BENCH_OUT_DIR", tmp_path)  # not the real $BENCH_OUT_DIR (#332)
    monkeypatch.setattr(verify.importlib, "import_module", lambda name: ModuleType(name))
    monkeypatch.setattr(h, "run_case", lambda module, ctx: current["result"])
    monkeypatch.setattr(verify.harness, "run_case", lambda module, ctx: current["result"])
    codes = {}
    for label, result in results.items():
        current["result"] = result
        codes[label] = verify.main(["--quick", "--only", "clickstream"])
    assert codes == {"pass": 0, "failed check": 1, "undetected control": 1, "failed probe": 1}

    def missing(module, ctx):
        raise h.HarnessError("no baseline")

    monkeypatch.setattr(verify.harness, "run_case", missing)
    assert verify.main(["--only", "clickstream"]) == 2
    assert "no baseline" in capsys.readouterr().err
    assert verify.main(["--only", "nonexistent"]) == 2


def _probe(passed: bool) -> object:
    rep = h.Report("SIM-1 probe")
    rep.add("p", passed)
    return rep


def test_the_exit_code_test_leaves_the_benchmark_output_alone(monkeypatch, capsys, tmp_path):
    """The harness writes results.json under $BENCH_OUT_DIR; a test must not (#332)."""
    target = verify.BENCH_OUT_DIR / "simulation_1to1" / "results.json"
    before = target.stat().st_mtime_ns if target.exists() else None
    test_exit_codes(monkeypatch, capsys, tmp_path)
    assert (tmp_path / "simulation_1to1" / "results.json").is_file()
    assert (target.stat().st_mtime_ns if target.exists() else None) == before
