"""The safe-profile parity harness's named product-added column key ``validators`` (W3-12).

W3-12 gives every safe column the counts and rates of its column validators (never a value);
the baseline's format has no such key. ``safe_profile_1to1/verify.py`` sets it aside only after
checking it on both sides, as it does for the PII rates (#2): the baseline column must not have
it, and the product column must carry exactly the raw profile's ``validators``. Nothing here
needs the baseline's venv.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "benchmarks" / "vs_refengine" / "safe_profile_1to1"
sys.path.insert(0, str(HARNESS.parent))
sys.path.insert(0, str(HARNESS))
_spec = importlib.util.spec_from_file_location("safe_profile_1to1_verify_v", HARNESS / "verify.py")
assert _spec is not None and _spec.loader is not None
verify = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verify)

VALIDATORS = {"email": {"checked": 10, "invalid": 1, "invalid_rate": 0.1}}


def _raw() -> dict:
    return {
        "name": "t",
        "columns": {"c": {"validators": VALIDATORS}, "d": {"validators": None}},
    }


def _safe(c_validators=VALIDATORS) -> dict:
    return {
        "tables": {
            "t": {
                "columns": {
                    "c": {"name": "c", "validators": c_validators},
                    "d": {"name": "d", "validators": None},
                }
            }
        }
    }


def _ref() -> dict:
    return {"tables": {"t": {"columns": {"c": {"name": "c"}, "d": {"name": "d"}}}}}


def test_matching_validators_are_set_aside_and_nothing_else_changes() -> None:
    got = _safe()
    out: list[str] = []
    stripped = verify.check_validators(_ref(), got, _raw(), "v", out)
    assert out == []
    assert stripped == _ref()
    assert "validators" in got["tables"]["t"]["columns"]["c"]  # the input is not mutated


def test_a_baseline_column_with_validators_is_a_mismatch() -> None:
    ref = _ref()
    ref["tables"]["t"]["columns"]["c"]["validators"] = VALIDATORS
    out: list[str] = []
    verify.check_validators(ref, _safe(), _raw(), "v", out)
    assert any("baseline" in line and "validators" in line for line in out)


def test_a_product_column_without_validators_is_a_mismatch() -> None:
    got = _safe()
    del got["tables"]["t"]["columns"]["d"]["validators"]
    out: list[str] = []
    verify.check_validators(_ref(), got, _raw(), "v", out)
    assert any("v.tables.t.columns.d" in line and "validators" in line for line in out)


def test_validators_that_differ_from_the_raw_profile_are_a_mismatch() -> None:
    changed = {"email": {"checked": 10, "invalid": 2, "invalid_rate": 0.2}}
    out: list[str] = []
    verify.check_validators(_ref(), _safe(c_validators=changed), _raw(), "v", out)
    assert any("v.tables.t.columns.c.validators" in line for line in out)
