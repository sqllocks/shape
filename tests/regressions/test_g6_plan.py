"""G6: ``plan_reconstruction`` marked everything ``preserved`` without checking anything.

Each plan item is now checked against what the generator can build from the evidence; a profile's
plan comes from the schema that is actually fitted. Every test fails on the old code (all items were
``preserved``; a profile was not understood at all)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pyarrow as pa

import shape
from shape.generation.fidelity import plan_reconstruction


def _status(plan: Any, evidence: str) -> str:
    return next(i.status for i in plan.items if i.evidence == evidence)


def test_text_without_top_values_is_not_preserved() -> None:
    plan = plan_reconstruction({"columns": {"name": {"kind": "text", "distinct_estimate": 50}}})
    assert _status(plan, "column:name") == "not_modelled"


def test_text_with_top_values_is_only_approximate() -> None:
    evidence = {"columns": {"c": {"kind": "text", "topk": [["a", 6], ["b", 4]]}}}
    assert _status(plan_reconstruction(evidence), "column:c") == "approximate"


def test_a_correlation_naming_a_missing_column_is_unavailable() -> None:
    evidence = {
        "columns": {"a": {"kind": "numeric", "mean": 1, "variance_population": 1}},
        "relationships": {"correlations": [{"source": "a", "target": "ghost", "rho": 0.5}]},
    }
    plan = plan_reconstruction(evidence)
    assert _status(plan, "correlations:0") == "unavailable"
    assert not plan.executable


def test_foreign_keys_and_conditionals_are_checked() -> None:
    evidence = {
        "columns": {"s": {"kind": "text", "topk": [["A", 1]]}, "v": {"kind": "numeric"}},
        "relationships": {
            "foreign_keys": [{"field": "c"}, {"field": "c", "parent_count": 10}],
            "conditionals": [
                {"when": "s", "field": "v", "mean": 1},
                {"when": "nope", "field": "v"},
            ],
        },
    }
    plan = plan_reconstruction(evidence)
    assert [_status(plan, f"foreign_keys:{i}") for i in (0, 1)] == ["unavailable", "approximate"]
    assert [_status(plan, f"conditionals:{i}") for i in (0, 1)] == ["approximate", "unavailable"]


def test_numeric_is_never_reported_as_exactly_preserved() -> None:
    plan = plan_reconstruction({"columns": {"x": {"kind": "numeric", "mean": 1.0}}})
    assert _status(plan, "column:x") == "approximate"


def test_a_profile_is_planned_field_by_field() -> None:
    rng = np.random.default_rng(1)
    n = 2000
    table = pa.table(
        {
            "id": np.arange(n),
            "name": [f"person {i}" for i in range(n)],
            "score": rng.normal(10, 2, n),
        }
    )
    plan = plan_reconstruction(shape.profile(table, name="t"))
    assert plan.executable
    assert _status(plan, "t.name.cardinality") == "not_modelled"  # free text: values are synthetic
    assert _status(plan, "t.id.is_primary_key") == "preserved"
    assert _status(plan, "t.score.mean") == "approximate"
    assert _status(plan, "dataset.missingness_joint") == "not_modelled"
