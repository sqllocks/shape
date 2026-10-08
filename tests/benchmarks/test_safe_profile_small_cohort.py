"""The safe-profile parity harness's named difference for #395 (SEC-high).

A safe column with fewer non-null rows than its ``k`` releases no ``mean``, ``std``,
``quantiles``, ``bounds`` or ``distribution_params``; the baseline releases them.
``benchmarks/vs_refengine/safe_profile_1to1/verify.py`` withholds those keys, and only those, from
the baseline after checking both sides: the column is below k by the raw profile's counts, the
product holds null, and the baseline's ``mean`` and ``std`` are the raw profile's. Nothing here
needs the baseline's venv.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from shape.privacy.safe_profile import SafeConfig

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "benchmarks" / "vs_refengine" / "safe_profile_1to1"
sys.path.insert(0, str(HARNESS.parent))
sys.path.insert(0, str(HARNESS))
_spec = importlib.util.spec_from_file_location("safe_profile_1to1_small", HARNESS / "verify.py")
assert _spec is not None and _spec.loader is not None
verify = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verify)

STATS = {
    "mean": 1001.0,
    "std": 1.0,
    "quantiles": {"p50": 1001.0},
    "bounds": {"lo": 1000.0, "hi": 1002.0},
    "distribution_params": {"loc": 1000.0, "scale": 2.0},
}
NULLS = dict.fromkeys(STATS)


def _raw(rows: int = 3, nulls: int = 0) -> dict:
    return {
        "name": "t",
        "row_count": rows,
        "columns": {
            "c": {"null_count": nulls, "null_rate": nulls / rows, "mean": 1001.0, "std": 1.0}
        },
    }


def _doc(stats: dict, dtype: str = "integer", winsorized: bool | None = None) -> dict:
    flag = stats.get("bounds") is not None if winsorized is None else winsorized
    return {
        "tables": {"t": {"columns": {"c": {"name": "c", "dtype": dtype, **stats}}}},
        "redaction_manifest": {"tables": {"t": {"c": {"bounds_winsorized": flag}}}},
    }


def _run(
    ref: dict, got: dict, raw: dict, cfg: SafeConfig, unsafe: bool = False
) -> tuple[dict, list[str]]:
    out: list[str] = []
    return verify.check_small_cohorts(ref, got, raw, cfg, unsafe, "v", out), out


def test_a_column_below_k_is_withheld_on_the_baseline_only_after_both_sides_check() -> None:
    ref = _doc(STATS)
    got, out = _run(ref, _doc(NULLS), _raw(), SafeConfig())
    assert out == []
    assert got == _doc(NULLS)
    assert ref == _doc(STATS)  # the input is not changed
    diffs: list[str] = []
    verify._close(got, _doc(NULLS), "v", diffs)
    assert diffs == []


def test_other_fields_of_a_column_below_k_are_still_compared() -> None:
    withheld, out = _run(_doc(STATS), _doc(NULLS, dtype="string"), _raw(), SafeConfig())
    assert out == []
    diffs: list[str] = []
    verify._close(withheld, _doc(NULLS, dtype="string"), "v", diffs)
    assert diffs == ["v.tables.t.columns.c.dtype: 'integer' != 'string'"]


def test_a_product_that_releases_a_statistic_below_k_is_a_mismatch() -> None:
    for key in STATS:
        got = _doc({**NULLS, key: STATS[key]}, winsorized=False)
        _, out = _run(_doc(STATS), got, _raw(), SafeConfig())
        assert out == [f"v.tables.t.columns.c.{key}: 3 rows < k but the product releases it"]


def test_a_product_without_a_statistic_key_below_k_is_a_mismatch() -> None:
    got = _doc({k: v for k, v in NULLS.items() if k != "bounds"})
    _, out = _run(_doc(STATS), got, _raw(), SafeConfig())
    assert out == ["v.tables.t.columns.c.bounds: 3 rows < k but the product releases it"]


def test_a_baseline_mean_or_std_that_is_not_the_raw_profiles_is_a_mismatch() -> None:
    _, out = _run(_doc({**STATS, "mean": 1001.5}), _doc(NULLS), _raw(), SafeConfig())
    assert out == ["v.tables.t.columns.c.mean (baseline): 1001.0 != 1001.5"]
    _, out = _run(_doc({**STATS, "std": 2.0}), _doc(NULLS), _raw(), SafeConfig())
    assert out == ["v.tables.t.columns.c.std (baseline): 1.0 != 2.0"]


def test_a_column_at_k_is_not_withheld() -> None:
    k = SafeConfig().column_k("c")
    withheld, out = _run(_doc(STATS), _doc(NULLS), _raw(rows=k), SafeConfig())
    assert out == []
    assert withheld == _doc(STATS)  # so the ordinary comparison reports the nulls and the flag
    diffs: list[str] = []
    verify._close(withheld, _doc(NULLS), "v", diffs)
    assert len(diffs) == len(STATS) + 1


def test_nulls_count_against_k() -> None:
    k = SafeConfig().column_k("c")
    _, out = _run(_doc(STATS), _doc(NULLS), _raw(rows=k, nulls=1), SafeConfig())
    assert out == []
    withheld, _ = _run(_doc(STATS), _doc(NULLS), _raw(rows=k, nulls=1), SafeConfig())
    assert withheld == _doc(NULLS)


def test_the_configured_k_is_used() -> None:
    cfg = SafeConfig(k=2)
    withheld, out = _run(_doc(STATS), _doc(NULLS), _raw(rows=3), cfg)
    assert out == []
    assert withheld == _doc(STATS)  # 3 rows >= k=2: nothing is withheld


def test_unsafe_full_fidelity_withholds_nothing() -> None:
    withheld, out = _run(_doc(STATS), _doc(STATS), _raw(), SafeConfig(unsafe_full_fidelity=True))
    assert out == []
    assert withheld == _doc(STATS)


def test_the_unsafe_mapper_case_withholds_nothing() -> None:
    # VARIANTS["unsafe"] is SafeConfig() with the separate unsafe flag of to_safe_profile.
    withheld, out = _run(_doc(STATS), _doc(STATS), _raw(), SafeConfig(), unsafe=True)
    assert out == []
    assert withheld == _doc(STATS)


def test_the_harness_runs_the_check_in_every_mapper_case() -> None:
    source = (HARNESS / "verify.py").read_text("utf-8")
    assert "ref = check_small_cohorts(ref, got, raw, cfg, unsafe, name, diffs)" in source


def test_the_bounds_winsorized_flag_follows_the_withheld_bounds() -> None:
    ref = _doc(STATS)
    assert ref["redaction_manifest"]["tables"]["t"]["c"]["bounds_winsorized"] is True
    withheld, out = _run(ref, _doc(NULLS), _raw(), SafeConfig())
    assert out == []
    assert withheld["redaction_manifest"]["tables"]["t"]["c"]["bounds_winsorized"] is False
    assert ref["redaction_manifest"]["tables"]["t"]["c"]["bounds_winsorized"] is True


def test_a_product_manifest_that_says_winsorized_below_k_is_a_mismatch() -> None:
    _, out = _run(_doc(STATS), _doc(NULLS, winsorized=True), _raw(), SafeConfig())
    assert out == ["v.tables.t.columns.c: 3 rows < k but the manifest says bounds_winsorized"]


def test_a_baseline_flag_that_does_not_match_its_bounds_is_a_mismatch() -> None:
    _, out = _run(_doc(STATS, winsorized=False), _doc(NULLS), _raw(), SafeConfig())
    assert out == [
        "v.tables.t.columns.c: the baseline's bounds_winsorized does not match its bounds"
    ]


def test_the_flag_of_a_column_at_k_is_not_withheld() -> None:
    k = SafeConfig().column_k("c")
    withheld, out = _run(_doc(STATS), _doc(NULLS), _raw(rows=k), SafeConfig())
    assert out == []
    assert withheld["redaction_manifest"]["tables"]["t"]["c"]["bounds_winsorized"] is True
