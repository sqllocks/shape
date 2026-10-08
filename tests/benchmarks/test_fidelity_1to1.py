"""P4-09: the fidelity report scores every column and table like the baseline's comparator.

``benchmarks/vs_refengine/fidelity_1to1/fixtures/expected_scores.json`` holds what the baseline's
comparator gave for the pairs of ``golden_data.py`` (``golden.py --check`` proves the file still
equals its output); Shape's scores must match it to rounding. The pairs cover every branch of the
scoring: numeric, integer, decimal, nullable, categorical (chi-squared above and below its
critical value, an extra category), boolean, unique and constant text, numbers and ISO dates as
text, timestamps and dates, an all-null column, samples too small for the KS test and a kind
mismatch. Nothing here needs the baseline's venv. ``fidelity_1to1/run.py`` does the same
comparison on retail's datasets and on edge pairs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[2] / "benchmarks" / "vs_refengine" / "fidelity_1to1"
sys.path.insert(0, str(BENCH))

import golden_data  # noqa: E402

from shape.generation.report import compare_tables  # noqa: E402

EXPECTED = json.loads((BENCH / "fixtures" / "expected_scores.json").read_text())
REPORT = compare_tables(*golden_data.tables())
CASES = [(t, c) for t, tf in EXPECTED["tables"].items() for c in tf["columns"]]


@pytest.mark.parametrize(("table", "column"), CASES)
def test_column_scores_equal_the_baseline(table, column):
    got = REPORT.tables[table].columns[column].score
    assert got == pytest.approx(EXPECTED["tables"][table]["columns"][column], abs=1e-9)


@pytest.mark.parametrize("table", sorted(EXPECTED["tables"]))
def test_table_scores_equal_the_baseline(table):
    assert REPORT.tables[table].score == pytest.approx(EXPECTED["tables"][table]["score"], abs=1e-9)


def test_overall_score_equals_the_baseline():
    assert REPORT.overall_score == pytest.approx(EXPECTED["overall_score"], abs=1e-9)


def test_every_golden_column_is_compared():
    real, _ = golden_data.tables()
    assert {(t, c) for t, tb in real.items() for c in tb.column_names} == set(CASES)
