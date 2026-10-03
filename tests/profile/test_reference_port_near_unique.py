"""The benchmark's reference port follows the near-unique text rule (ISS-profile #37).

`verify.py` turns the Spindle baseline column into "no value list" for a string column with more
than 500 distinct values, at least 95% of its non-null count; the port must give the same, or
`run.py --quick` reports the reference_port profile rows as verifier=fail.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pyarrow as pa

ROOT = Path(__file__).resolve().parents[2]


def _port():
    path = ROOT / "benchmarks" / "vs_spindle" / "profile_1to1" / "port.py"
    spec = importlib.util.spec_from_file_location("vs_spindle_port", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["vs_spindle_port"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_near_unique_text_lists_no_values_and_repeating_text_still_does():
    n = 1200
    table = pa.table(
        {
            "email": [f"u{i}@x.com" for i in range(n)],
            "city": [f"c{i % 600}" for i in range(n)],  # 600 distinct of 1200: not near-unique
            "code": [f"k{i % 20}" for i in range(n)],
        }
    )
    prof = _port().profile_table(table)
    cols = prof.columns
    assert cols["email"].value_counts_ext is None
    assert cols["city"].value_counts_ext and len(cols["city"].value_counts_ext) == 500
    assert cols["code"].value_counts_ext and len(cols["code"].value_counts_ext) == 20
