"""W1-08 (#66): the planted-drift regression sweep.

Every pair of datasets here comes from Shape's own generators: ``DriftPlan`` (the engine-native
drift of ISS-diff #14), the row counts of the generation engine, and the named corruptions of
``shape.chaos.groundtruth`` (the chaos generator). Each planted change is written to an answer key
(column, kind, size), ``shape diff`` runs at its default thresholds, and two things are asserted:

* **zero false negatives:** every planted change is reported as one of the diff kinds that find it;
* **a bounded false-positive rate:** pairs of the same distribution (fresh samples, and extracts of
  different sizes) report almost nothing. The bounds are in ``docs/DRIFT.md`` ("The sweep").

``SHAPE_DRIFT_SWEEP=fast`` (the default, CI) or ``full`` (the nightly job) sets the size. Run
``python tests/diff/test_drift_sweep.py`` to print the whole report.
"""

from __future__ import annotations

import copy
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import pytest

import shape
from shape.chaos.groundtruth import Corruption, corrupt_tables
from shape.generation.drift_plan import DriftPlan
from shape.generation.schema import GenSchema

SIZES: dict[str, dict[str, Any]] = {
    "fast": {"rows": 2000, "trials": 5, "null_trials": 25},
    "full": {"rows": 20000, "trials": 40, "null_trials": 200},
}
MODE = os.environ.get("SHAPE_DRIFT_SWEEP", "fast")
if MODE not in SIZES:
    raise RuntimeError(f"SHAPE_DRIFT_SWEEP must be one of {sorted(SIZES)}, got {MODE!r}")
SIZE = SIZES[MODE]

# The documented bounds (docs/DRIFT.md, "The sweep"): of the same-distribution pairs, at most this
# share report any change at all, and at most this share of (pair, column) comparisons do.
MAX_PAIRS_WITH_A_CHANGE = 0.05
MAX_COLUMN_FALSE_POSITIVE_RATE = 0.005

SCHEMA_DOC: dict[str, Any] = {
    "schema_version": 1,
    "model": {"name": "feed", "seed": 7, "schema_mode": "3nf"},
    "tables": {
        "orders": {
            "name": "orders",
            "primary_key": ["order_id"],
            "columns": {
                "order_id": {
                    "name": "order_id",
                    "type": "integer",
                    "generator": {"strategy": "sequence"},
                },
                "status": {
                    "name": "status",
                    "type": "string",
                    "generator": {
                        "strategy": "weighted_enum",
                        "values": {"completed": 80, "shipped": 15, "cancelled": 5},
                    },
                },
                "channel": {
                    "name": "channel",
                    "type": "string",
                    "generator": {
                        "strategy": "weighted_enum",
                        "values": {"web": 50, "store": 30, "app": 15, "phone": 5},
                    },
                },
                "total": {
                    "name": "total",
                    "type": "float",
                    "generator": {
                        "strategy": "distribution",
                        "distribution": "log_normal",
                        "mean": 4.0,
                        "sigma": 0.5,
                    },
                },
                "amount": {
                    "name": "amount",
                    "type": "float",
                    "generator": {
                        "strategy": "distribution",
                        "distribution": "normal",
                        "mean": 50.0,
                        "std_dev": 10.0,
                    },
                },
                "score": {
                    "name": "score",
                    "type": "float",
                    "generator": {
                        "strategy": "distribution",
                        "distribution": "uniform",
                        "min": 0.0,
                        "max": 100.0,
                    },
                },
                "qty": {
                    "name": "qty",
                    "type": "integer",
                    "generator": {"strategy": "weighted_enum", "values": {"1": 5, "2": 3, "3": 2}},
                },
                "note": {
                    "name": "note",
                    "type": "string",
                    "nullable": True,
                    "null_rate": 0.02,
                    "generator": {"strategy": "weighted_enum", "values": {"a": 1, "b": 1}},
                },
                "vip": {
                    "name": "vip",
                    "type": "integer",
                    "generator": {
                        "strategy": "distribution",
                        "distribution": "bernoulli",
                        "probability": 0.5,
                    },
                },
            },
        }
    },
    "generation": {"scale": "small", "scales": {"small": {"orders": 2000}}},
}

_STRING_TYPE = {
    "type": "string",
    "generator": {"strategy": "weighted_enum", "values": {"y": 1, "n": 1}},
}


def _event(kind: str, column: str, **spec: Any) -> dict[str, Any]:
    return {"kind": kind, "table": "orders", "column": column, "start": 1, **spec}


# engine-native drift: one event, planted at day 1 (day 0 is the baseline), with its size in the
# unit the diff measures it in (a rate or distance, a scale factor, or 1 for a structural change)
DRIFT_CASES: dict[str, tuple[dict[str, Any], tuple[str, ...], float]] = {
    "null_rate": (_event("null_rate", "note", to=0.25), ("null_rate_change",), 0.23),
    "category_weights": (
        _event(
            "category_weights", "status", weights={"completed": 45, "shipped": 35, "cancelled": 20}
        ),
        ("category_shift", "new_categorical_values"),
        0.35,
    ),
    "category_weights_channel": (
        _event(
            "category_weights", "channel", weights={"web": 20, "store": 30, "app": 15, "phone": 35}
        ),
        ("category_shift",),
        0.30,
    ),
    "new_category": (
        _event("new_category", "status", value="lost", share=0.08),
        ("new_categorical_values",),
        0.08,
    ),
    "lognormal_scale": (
        _event("distribution", "total", scale=1.4),
        ("mean_shift", "distribution_shift"),
        1.4,
    ),
    "normal_scale": (
        _event("distribution", "amount", scale=1.3),
        ("mean_shift", "distribution_shift"),
        1.3,
    ),
    "uniform_scale": (
        _event("distribution", "score", scale=1.5),
        ("mean_shift", "distribution_shift", "spread_change"),
        1.5,
    ),
    "bernoulli_rate": (
        _event("distribution", "vip", params={"probability": 0.8}),
        ("true_rate_change",),
        0.3,
    ),
    "add_column": (
        _event(
            "add_column",
            "region",
            definition={
                "type": "string",
                "generator": {"strategy": "weighted_enum", "values": {"n": 1, "s": 1}},
            },
        ),
        ("column_added",),
        1.0,
    ),
    "drop_column": (_event("drop_column", "score"), ("column_removed",), 1.0),
    "type_change": (_event("type_change", "qty", to=_STRING_TYPE), ("dtype_change",), 1.0),
}

# the corruptions of the chaos generator, and the diff kinds that find each
CHAOS_CASES: dict[str, tuple[Corruption, tuple[str, ...]]] = {
    "case_whitespace": (
        Corruption("case_whitespace", 0.3, "orders", "status"),
        ("new_categorical_values", "category_shift"),
    ),
    "null_creep": (
        Corruption("null_creep", 0.3, "orders", "note", options={"step": 0.0}),
        ("null_rate_change",),
    ),
    # the profiler types a column from its values, so digits delivered as text keep their type;
    # what shows is the way the values are written ("1.0" became "1")
    "type_change": (
        Corruption("type_change", 1.0, "orders", "qty"),
        ("dtype_change", "new_categorical_values", "category_shift"),
    ),
    "negative_amounts": (
        Corruption("negative_amounts", 0.3, "orders", "total"),
        ("mean_shift", "distribution_shift", "range_change"),
    ),
    "duplicates": (
        Corruption("duplicates", 0.6, "orders"),
        ("uniqueness_change", "cardinality_change"),
    ),
    "pii_fill": (
        Corruption("pii_fill", 0.5, "orders", "note", options={"pii": "email"}),
        ("pattern_change", "length_change", "category_shift", "new_categorical_values"),
    ),
}

# row counts (the generation engine's own scale); the ratio is beyond the 2x / half defaults
ROW_COUNT_CASES: dict[str, float] = {"rows_up": 2.5, "rows_down": 0.4}


@dataclass
class Planted:
    """One answer-key entry."""

    case: str
    source: str  # drift_plan, chaos or generator
    column: str | None  # None: the table
    planted: str
    size: float
    detected_as: tuple[str, ...]
    reported: list[str] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return any(k in self.reported for k in self.detected_as)


def schema_for(trial: int) -> GenSchema:
    doc = copy.deepcopy(SCHEMA_DOC)
    doc["model"]["seed"] = 1000 + trial
    return GenSchema.from_dict(doc)


def profile_of(table: Any) -> Any:
    return shape.profile(table, name="orders")


def _reported(diff: Any, column: str | None) -> list[str]:
    return [c["kind"] for c in diff.changes if c["column"] == column]


def planted_pairs(trial: int, rows: int) -> list[tuple[Planted, Any, Any]]:
    """``(answer-key entry, baseline profile, current profile)`` for every planted case."""
    schema = schema_for(trial)
    base_plan = DriftPlan([], start="2026-03-01", days=2)
    base_table = base_plan.generate_day(schema, 0, row_counts={"orders": rows})["orders"]
    baseline = profile_of(base_table)
    out: list[tuple[Planted, Any, Any]] = []
    for case, (event, kinds, size) in DRIFT_CASES.items():
        plan = DriftPlan([event], start="2026-03-01", days=2)
        (expected,) = plan.expected_changes(0, 1)  # the plan's own key: one event, full effect
        assert expected["size"] == 1.0
        today = plan.generate_day(schema, 1, row_counts={"orders": rows})["orders"]
        entry = Planted(case, "drift_plan", expected["column"], event["kind"], size, kinds)
        out.append((entry, baseline, profile_of(today)))
    # chaos corruptions are planted into the same day-1 sample the quiet pairs use
    day1 = base_plan.generate_day(schema, 1, row_counts={"orders": rows})["orders"]
    keys = {"orders": "order_id"}
    for case, (corruption, kinds) in CHAOS_CASES.items():
        outcome = corrupt_tables({"orders": day1}, [corruption], seed=trial, keys=keys)
        (applied,) = outcome.applied
        assert applied["kind"] == corruption.kind
        entry = Planted(
            f"chaos_{case}",
            "chaos",
            # duplicates copies whole rows; the copies show on the table's key column
            corruption.column or keys["orders"],
            corruption.kind,
            applied["rows"] / rows,
            kinds,
        )
        out.append((entry, baseline, profile_of(outcome.tables["orders"])))
    for case, ratio in ROW_COUNT_CASES.items():
        today = base_plan.generate_day(schema, 1, row_counts={"orders": round(rows * ratio)})
        entry = Planted(case, "generator", None, "row_count", ratio, ("row_count_change",))
        out.append((entry, baseline, profile_of(today["orders"])))
    return out


def quiet_pairs(trials: int, rows: int) -> list[tuple[str, Any, Any]]:
    """Pairs of one distribution: a fresh sample of the same size, and a fresh sample 1.5 times as
    large (an extract of another size). No event is planted, so the answer key is empty."""
    out = []
    for trial in range(trials):
        schema = schema_for(10_000 + trial)
        plan = DriftPlan([], start="2026-03-01", days=3)
        a = profile_of(plan.generate_day(schema, 0, row_counts={"orders": rows})["orders"])
        same = profile_of(plan.generate_day(schema, 1, row_counts={"orders": rows})["orders"])
        big = profile_of(
            plan.generate_day(schema, 2, row_counts={"orders": rows * 3 // 2})["orders"]
        )
        out += [(f"same_size_{trial}", a, same), (f"larger_extract_{trial}", a, big)]
    return out


def run_planted(trials: int, rows: int) -> list[Planted]:
    keys = []
    for trial in range(trials):
        for entry, baseline, today in planted_pairs(trial, rows):
            entry.reported = _reported(shape.diff(baseline, today), entry.column)
            keys.append(entry)
    return keys


def run_quiet(trials: int, rows: int) -> tuple[int, int, Counter[tuple[str | None, str]]]:
    """``(pairs, pairs with any change, changes by (column, kind))``."""
    pairs = noisy = 0
    seen: Counter[tuple[str | None, str]] = Counter()
    for _, a, b in quiet_pairs(trials, rows):
        changes = shape.diff(a, b).changes
        pairs += 1
        noisy += bool(changes)
        seen.update((c["column"], c["kind"]) for c in changes)
    return pairs, noisy, seen


@pytest.fixture(scope="module")
def planted() -> list[Planted]:
    return run_planted(SIZE["trials"], SIZE["rows"])


@pytest.fixture(scope="module")
def quiet() -> tuple[int, int, Counter[tuple[str | None, str]]]:
    return run_quiet(SIZE["null_trials"], SIZE["rows"])


def test_the_answer_key_lists_every_case_with_column_kind_and_size(planted):
    cases = {p.case for p in planted}
    expected = set(DRIFT_CASES) | {f"chaos_{c}" for c in CHAOS_CASES} | set(ROW_COUNT_CASES)
    assert cases == expected
    for p in planted:
        assert p.planted and p.size > 0 and p.detected_as
        assert (p.column is None) == (p.source == "generator")


def test_no_planted_change_is_missed(planted):
    missed = [
        (p.case, p.column, p.planted, round(p.size, 3), p.reported) for p in planted if not p.found
    ]
    assert missed == []


def test_every_planted_case_is_found_in_every_trial(planted):
    per_case = Counter(p.case for p in planted)
    assert set(per_case.values()) == {SIZE["trials"]}


def test_same_distribution_pairs_stay_under_the_false_positive_bounds(quiet):
    pairs, noisy, seen = quiet
    columns = len(SCHEMA_DOC["tables"]["orders"]["columns"])
    assert pairs == 2 * SIZE["null_trials"]
    assert noisy / pairs <= MAX_PAIRS_WITH_A_CHANGE, seen
    assert sum(seen.values()) / (pairs * columns) <= MAX_COLUMN_FALSE_POSITIVE_RATE, seen


def test_distribution_change_stays_quiet_on_same_distribution_pairs(quiet):
    _, _, seen = quiet
    assert [k for k in seen if k[1] == "distribution_change"] == []


def test_row_count_change_never_fires_on_same_distribution_pairs(quiet):
    _, _, seen = quiet
    assert [k for k in seen if k[1] == "row_count_change"] == []


def test_a_diff_that_misses_a_planted_change_fails_the_sweep(planted):
    # the sweep is the regression test: with the detector blinded to one kind it must fail
    blind = [
        Planted(
            p.case,
            p.source,
            p.column,
            p.planted,
            p.size,
            p.detected_as,
            [k for k in p.reported if k != "null_rate_change"],
        )
        for p in planted
    ]
    assert [p.case for p in blind if not p.found]


def report() -> None:
    planted_keys = run_planted(SIZE["trials"], SIZE["rows"])
    print(f"mode={MODE} rows={SIZE['rows']} trials={SIZE['trials']}")
    by_case: dict[str, list[Planted]] = {}
    for p in planted_keys:
        by_case.setdefault(p.case, []).append(p)
    for case, entries in by_case.items():
        hit = sum(p.found for p in entries)
        print(f"  {case:28s} {hit}/{len(entries)}  size~{entries[0].size:.3f}")
    pairs, noisy, seen = run_quiet(SIZE["null_trials"], SIZE["rows"])
    columns = len(SCHEMA_DOC["tables"]["orders"]["columns"])
    print(f"quiet pairs={pairs} with-a-change={noisy} ({noisy / pairs:.3f})")
    print(f"column false-positive rate={sum(seen.values()) / (pairs * columns):.4f}")
    for key, n in seen.most_common():
        print("   ", key, n)


if __name__ == "__main__":
    report()
    sys.exit(0)
