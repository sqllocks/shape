"""The safe-profile parity harness's named product-added keys: the PII pattern rates (#2).

ISS-profile (issue #2, integrated in INT-13) gives every safe column ``pattern_rates`` and
``pattern_contains_rates``: aggregate shares, never values, which the baseline's format does not
have. ``benchmarks/vs_refengine/safe_profile_1to1/verify.py`` sets them aside only after checking
them on both sides: the baseline column must not have them, and the product column must carry
exactly the raw profile's rates. Nothing here needs the baseline's venv.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "benchmarks" / "vs_refengine" / "safe_profile_1to1"
sys.path.insert(0, str(HARNESS.parent))
sys.path.insert(0, str(HARNESS))
_spec = importlib.util.spec_from_file_location("safe_profile_1to1_verify", HARNESS / "verify.py")
assert _spec is not None and _spec.loader is not None
verify = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verify)

RATES = {"ssn": 0.25, "email": 0.0}
CONTAINS = {"ssn": 0.5}


def _raw() -> dict:
    return {
        "name": "t",
        "columns": {
            "c": {"pattern_rates": RATES, "pattern_contains_rates": CONTAINS},
            "d": {"pattern_rates": None, "pattern_contains_rates": None},
        },
    }


def _safe(c_rates=RATES, c_contains=CONTAINS) -> dict:
    return {
        "tables": {
            "t": {
                "columns": {
                    "c": {
                        "name": "c",
                        "pattern_rates": c_rates,
                        "pattern_contains_rates": c_contains,
                    },
                    "d": {"name": "d", "pattern_rates": None, "pattern_contains_rates": None},
                }
            }
        }
    }


def _ref() -> dict:
    return {"tables": {"t": {"columns": {"c": {"name": "c"}, "d": {"name": "d"}}}}}


def test_matching_rates_are_set_aside_and_nothing_else_changes() -> None:
    got = _safe()
    out: list[str] = []
    stripped = verify.check_pii_rates(_ref(), got, _raw(), "v", out)
    assert out == []
    assert stripped == _ref()
    assert "pattern_rates" in got["tables"]["t"]["columns"]["c"]  # the input is not mutated


def test_a_baseline_column_with_the_keys_is_a_mismatch() -> None:
    ref = _ref()
    ref["tables"]["t"]["columns"]["c"]["pattern_rates"] = RATES
    out: list[str] = []
    verify.check_pii_rates(ref, _safe(), _raw(), "v", out)
    assert any("baseline" in line and "pattern_rates" in line for line in out)


def test_a_product_column_without_the_keys_is_a_mismatch() -> None:
    got = _safe()
    del got["tables"]["t"]["columns"]["d"]["pattern_contains_rates"]
    out: list[str] = []
    verify.check_pii_rates(_ref(), got, _raw(), "v", out)
    assert any("v.tables.t.columns.d" in line and "pattern_contains_rates" in line for line in out)


def test_rates_that_differ_from_the_raw_profile_are_a_mismatch() -> None:
    out: list[str] = []
    verify.check_pii_rates(_ref(), _safe(c_rates={"ssn": 0.26, "email": 0.0}), _raw(), "v", out)
    assert any("v.tables.t.columns.c.pattern_rates" in line for line in out)


def test_rates_on_a_column_the_raw_profile_lacks_are_a_mismatch() -> None:
    raw = _raw()
    del raw["columns"]["d"]
    out: list[str] = []
    verify.check_pii_rates(_ref(), _safe(), raw, "v", out)
    assert any("v.tables.t.columns.d" in line for line in out)
