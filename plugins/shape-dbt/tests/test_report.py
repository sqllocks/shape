"""One report for a dbt run and a Shape check and drift comparison."""

from __future__ import annotations

import json

import pytest
from shape_dbt.report import ReportError, build_report, dbt_findings, load_json, render_markdown

MANIFEST = {
    "nodes": {
        "model.p.orders": {"resource_type": "model", "name": "orders"},
        "test.p.not_null_orders_status.1": {
            "resource_type": "test",
            "name": "not_null_orders_status",
            "attached_node": "model.p.orders",
            "column_name": "status",
            "tags": ["shape"],
            "test_metadata": {"name": "not_null", "kwargs": {"column_name": "status"}},
        },
        "test.p.range.2": {
            "resource_type": "test",
            "name": "dbt_utils_accepted_range_orders_amount",
            "attached_node": "model.p.orders",
            "column_name": "amount",
            "tags": [],
            "test_metadata": {"name": "accepted_range", "namespace": "dbt_utils", "kwargs": {}},
        },
    },
    "sources": {},
}


def run_results(*statuses):
    uids = ["model.p.orders", "test.p.not_null_orders_status.1", "test.p.range.2"]
    return {
        "metadata": {"dbt_version": "1.11.0", "generated_at": "2026-10-02T00:00:00Z"},
        "results": [
            {"unique_id": u, "status": s, "message": m, "failures": f, "execution_time": 0.1}
            for u, (s, m, f) in zip(uids, statuses, strict=True)
        ],
    }


GREEN = run_results(("success", "OK", None), ("pass", None, 0), ("pass", None, 0))
RED = run_results(("success", "OK", None), ("fail", "Got 3 results", 3), ("pass", None, 0))
CHECK_FAIL = {
    "passed": False,
    "violations": [
        {"column": "orders.status", "rule": "allowed_values", "expected": ["a"], "observed": "b"}
    ],
}
DRIFT = {
    "drifted": True,
    "changes": [
        {"column": "orders.status", "kind": "category_shift", "severity": "medium"},
        {"column": "orders.amount", "kind": "mean_shift", "severity": "medium"},
    ],
}


def test_a_green_run_is_ok():
    report = build_report(GREEN, MANIFEST)
    assert report["ok"] is True
    assert report["summary"] == {
        "dbt_failed": 0,
        "dbt_warned": 0,
        "dbt_total": 3,
        "contract_violations": 0,
        "drift_changes": 0,
    }
    assert report["dbt"]["counts"] == {"pass": 2, "success": 1}
    assert report["format"] == "shape-dbt-report" and report["version"] == 1


def test_a_failed_test_is_resolved_through_the_manifest():
    report = build_report(RED, MANIFEST)
    assert report["ok"] is False
    (failed,) = report["dbt"]["failed"]
    assert (failed["model"], failed["column"], failed["test"]) == ("orders", "status", "not_null")
    assert failed["failures"] == 3 and failed["tags"] == ["shape"]


def test_a_namespaced_test_keeps_its_package():
    findings = dbt_findings(GREEN, MANIFEST)
    assert findings[2]["test"] == "dbt_utils.accepted_range"


def test_without_a_manifest_the_findings_still_carry_the_node_id():
    (failed,) = build_report(RED)["dbt"]["failed"]
    assert failed["unique_id"] == "test.p.not_null_orders_status.1"
    assert failed["name"] == "1"  # without the manifest the id's last part is the name


def test_an_error_fails_and_a_warning_does_not():
    errored = run_results(("error", "boom", None), ("pass", None, 0), ("pass", None, 0))
    assert build_report(errored, MANIFEST)["ok"] is False
    warned = run_results(("success", "OK", None), ("warn", "Got 1 result", 1), ("pass", None, 0))
    report = build_report(warned, MANIFEST)
    assert report["ok"] is True
    assert report["summary"]["dbt_warned"] == 1 and report["dbt"]["warned"][0]["status"] == "warn"


def test_a_contract_violation_fails_the_report_even_when_dbt_is_green():
    report = build_report(GREEN, MANIFEST, check=CHECK_FAIL)
    assert report["ok"] is False and report["summary"]["contract_violations"] == 1


def test_drift_fails_only_when_asked():
    assert build_report(GREEN, MANIFEST, drift=DRIFT)["ok"] is True
    assert build_report(GREEN, MANIFEST, drift=DRIFT, fail_on_drift=True)["ok"] is False
    assert build_report(GREEN, MANIFEST, drift={"drifted": False, "changes": []}, fail_on_drift=True)[
        "ok"
    ]


def test_one_column_flagged_by_dbt_and_shape_is_shown_together():
    report = build_report(RED, MANIFEST, check=CHECK_FAIL, drift=DRIFT)
    assert list(report["by_column"]) == ["orders.status"]
    parts = report["by_column"]["orders.status"]
    assert (len(parts["dbt"]), len(parts["contract"]), len(parts["drift"])) == (1, 1, 1)


def test_a_single_table_check_meets_the_dbt_findings_through_the_table_name():
    check = {"passed": False, "violations": [{"column": "status", "rule": "r", "expected": 1, "observed": 2}]}
    assert build_report(RED, MANIFEST, check=check, table="orders")["by_column"]
    assert not build_report(RED, MANIFEST, check=check)["by_column"]


def test_the_markdown_has_one_section_per_source():
    text = render_markdown(build_report(RED, MANIFEST, check=CHECK_FAIL, drift=DRIFT))
    assert text.startswith("# dbt and Shape: FAIL")
    for heading in ("## dbt failures", "## Contract violations", "## Drift", "## Columns flagged"):
        assert heading in text
    assert "| fail | orders | status | not_null |" in text
    clean = render_markdown(build_report(GREEN, MANIFEST))
    assert "PASS" in clean and "not run" in clean and "## dbt failures" not in clean


def test_a_pipe_in_a_message_does_not_break_the_table():
    bad = run_results(("fail", "a | b", 1), ("pass", None, 0), ("pass", None, 0))
    assert "a \\| b" in render_markdown(build_report(bad, MANIFEST))


def test_a_file_that_is_not_run_results_is_refused(tmp_path):
    with pytest.raises(ReportError, match="no 'results' list"):
        dbt_findings({"nodes": {}})
    bad = tmp_path / "x.json"
    bad.write_text("nope", encoding="utf-8")
    with pytest.raises(ReportError, match="not readable JSON"):
        load_json(bad, "run_results")
    with pytest.raises(ReportError, match="not readable JSON"):
        load_json(tmp_path / "missing.json", "manifest")


def test_the_cli_reports_and_sets_the_exit_code(tmp_path, capsys):
    from shape.plugins import cli
    from shape.plugins.host import default_host

    rr, mf, ck, df = (tmp_path / n for n in ("rr.json", "mf.json", "ck.json", "df.json"))
    mf.write_text(json.dumps(MANIFEST), encoding="utf-8")
    ck.write_text(json.dumps(CHECK_FAIL), encoding="utf-8")
    df.write_text(json.dumps(DRIFT), encoding="utf-8")
    out, md = tmp_path / "report.json", tmp_path / "report.md"
    base = ["--manifest", str(mf), "-o", str(out), "--md", str(md)]
    rr.write_text(json.dumps(GREEN), encoding="utf-8")
    assert cli.run_command(default_host(), "dbt-report", ["--run-results", str(rr), *base]) == 0
    rr.write_text(json.dumps(RED), encoding="utf-8")
    code = cli.run_command(
        default_host(),
        "dbt-report",
        ["--run-results", str(rr), *base, "--check-result", str(ck), "--diff-result", str(df)],
    )
    assert code == 1
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["ok"] is False and "orders.status" in doc["by_column"]
    assert md.read_text(encoding="utf-8").startswith("# dbt and Shape: FAIL")
    assert "dbt failures" in capsys.readouterr().out


def test_the_cli_computes_the_check_and_the_drift_from_profiles(tmp_path):
    import pyarrow as pa
    from shape.plugins import cli
    from shape.plugins.host import default_host

    import shape

    base = shape.profile(pa.table({"amount": [1.0, 2.0, 3.0] * 30}), name="orders")
    now = shape.profile(pa.table({"amount": [10.0, 20.0, 30.0] * 30}), name="orders")
    shape.save(base, tmp_path / "base.shape")
    shape.save(now, tmp_path / "now.shape")
    contract = tmp_path / "c.json"
    contract.write_text(json.dumps({"columns": {"amount": {"max": 5}}}), encoding="utf-8")
    rr, out = tmp_path / "rr.json", tmp_path / "out.json"
    rr.write_text(json.dumps(GREEN), encoding="utf-8")
    code = cli.run_command(
        default_host(),
        "dbt-report",
        [
            "--run-results", str(rr),
            "--profile", str(tmp_path / "now.shape"),
            "--contract", str(contract),
            "--baseline", str(tmp_path / "base.shape"),
            "--table", "orders",
            "--fail-on-drift",
            "-o", str(out),
        ],
    )  # fmt: skip
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert code == 1
    assert doc["summary"]["contract_violations"] == 1 and doc["summary"]["drift_changes"] >= 1
    assert doc["shape"]["drift"]["drifted"] is True


def test_the_cli_refuses_a_contract_without_a_profile(tmp_path, capsys):
    from shape.plugins import cli
    from shape.plugins.host import default_host

    rr = tmp_path / "rr.json"
    rr.write_text(json.dumps(GREEN), encoding="utf-8")
    assert (
        cli.run_command(
            default_host(), "dbt-report", ["--run-results", str(rr), "--contract", "c.json"]
        )
        == 2
    )
    assert "need --profile" in capsys.readouterr().err
