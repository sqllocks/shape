"""Item 3 without MLflow: metric flattening, input checks and the missing-extra exit."""

from __future__ import annotations

import json
import math

import pytest
from shape_integrations import mlflow_log

MISSING = "MLflow needs the 'mlflow' extra: pip install 'sqllocks-shape-integrations[mlflow]'"

REPORT = {
    "shape_version": "0.9.0",
    "passed": False,
    "row_counts": {"customer": 3},
    "gates": [
        {
            "gate": "schema_conformance",
            "passed": True,
            "errors": [],
            "warnings": ["w"],
            "details": {"tables_checked": 2, "ratio": 0.5, "note": "text", "flag": True},
        },
        {
            "gate": "memorization",
            "passed": False,
            "errors": ["e1", "e2"],
            "warnings": [],
            "details": {"nearest_distance": {"p05": 0.25}, "bad": float("nan"), "inf": math.inf},
        },
    ],
}


def run(*argv: str) -> int:
    from shape.plugins.cli import run_command
    from shape.plugins.host import default_host

    return run_command(default_host(), "mlflow", list(argv))


def test_metrics_hold_outcome_counts_and_numeric_details():
    m = mlflow_log.metrics_from_report(REPORT)
    assert m["passed"] == 0.0
    assert m["gate.schema_conformance.passed"] == 1.0
    assert m["gate.schema_conformance.errors"] == 0.0
    assert m["gate.schema_conformance.warnings"] == 1.0
    assert m["gate.schema_conformance.tables_checked"] == 2.0
    assert m["gate.schema_conformance.ratio"] == 0.5
    assert m["gate.memorization.passed"] == 0.0
    assert m["gate.memorization.errors"] == 2.0
    assert m["gate.memorization.nearest_distance.p05"] == 0.25


def test_metrics_skip_text_booleans_and_non_finite_numbers():
    m = mlflow_log.metrics_from_report(REPORT)
    assert "gate.schema_conformance.note" not in m
    assert "gate.schema_conformance.flag" not in m
    assert "gate.memorization.bad" not in m
    assert "gate.memorization.inf" not in m


def test_metric_names_use_only_characters_mlflow_accepts():
    report = {"passed": True, "gates": [{"gate": "a b/c:d", "passed": True, "details": {"k*": 1}}]}
    names = mlflow_log.metrics_from_report(report)
    assert all(all(c.isalnum() or c in "_-. /" for c in n) for n in names), sorted(names)
    assert "gate.a b/c_d.k_" in names


@pytest.mark.parametrize(
    "report",
    [[], {"gates": "x"}, {"gates": [1]}, {"gates": [{"passed": True}]}, {"no": "gates"}],
)
def test_a_report_that_is_not_a_verify_report_is_refused(report):
    with pytest.raises(mlflow_log.ReportError):
        mlflow_log.metrics_from_report(report)


def test_without_the_library_the_command_exits_2_with_the_pip_command(
    manifest, capsys, hide_library
):
    hide_library("mlflow")
    assert run("log", str(manifest)) == 2
    assert capsys.readouterr().err.strip() == f"shape: error: {MISSING}"


def test_input_files_are_checked_before_the_library_is_needed(manifest, tmp_path, capsys):
    assert run("log", str(tmp_path / "nope.json")) == 2
    assert "manifest not found" in capsys.readouterr().err
    assert run("log", str(manifest), "--profile", str(tmp_path / "p.shape")) == 2
    assert "profile not found" in capsys.readouterr().err
    assert run("log", str(manifest), "--verify-report", str(tmp_path / "r.json")) == 2
    assert "verify report not found" in capsys.readouterr().err
    bad = tmp_path / "r.json"
    bad.write_text(json.dumps({"no": "gates"}))
    assert run("log", str(manifest), "--verify-report", str(bad)) == 2
    bad.write_text("{")
    assert run("log", str(manifest), "--verify-report", str(bad)) == 2


def test_an_empty_experiment_name_exits_2(manifest, capsys):
    assert run("log", str(manifest), "--experiment", "  ") == 2
