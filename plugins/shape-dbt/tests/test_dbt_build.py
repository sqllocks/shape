"""The acceptance test of the dbt integration: a jaffle-shop-style dbt project is imported with
`from-dbt`, its data is generated and loaded as seeds, `dbt build` runs against DuckDB, the model
outputs are profiled, and a compiled test set round-trips and runs in dbt. Then a drifted model
makes a dbt test fail, and one report shows the failed test and Shape's finding together.

Needs `dbt-duckdb` (the CI `dbt` job installs it; core never depends on it) and, for
`dbt deps`, network access to the dbt package hub. On a runner without that access, point
`SHAPE_DBT_PACKAGES_FILE` at a `packages.yml` with `local:` or `git:` entries for dbt_utils,
dbt_expectations and dbt_date. Run with `pytest -m dbt`; the rest of the plugin's tests run
with `-m "not dbt"`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pytest
import yaml
from shape_dbt.totests import (
    bounds_from_profile,
    compile_tests,
    contract_from_profile,
    contract_from_schema_yaml,
    expressible,
    merge_schema_docs,
    normalize_contract,
    render_yaml,
)

import shape
from shape.plugins import cli
from shape.plugins.host import default_host

pytestmark = pytest.mark.dbt

ROWS = "raw_customers=150,raw_orders=600,raw_payments=1500"
MARTS = ("orders", "customers")


def dbt_exe() -> str:
    found = shutil.which("dbt") or str(Path(sys.executable).with_name("dbt"))
    assert Path(found).exists(), "dbt is not installed here: pip install dbt-duckdb"
    return found


def dbt(project: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "DBT_SEND_ANONYMOUS_USAGE_STATS": "false", "NO_COLOR": "1"}
    run = subprocess.run(
        [dbt_exe(), *args, "--profiles-dir", str(project), "--project-dir", str(project)],
        cwd=project,
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
    )
    if check:
        assert run.returncode == 0, f"dbt {' '.join(args)} failed:\n{run.stdout}\n{run.stderr}"
    return run


def run_results(project: Path) -> dict[str, Any]:
    return json.loads((project / "target" / "run_results.json").read_text(encoding="utf-8"))


def marts(project: Path) -> dict[str, pa.Table]:
    con = duckdb.connect(str(project / "target" / "jaffle_shop.duckdb"), read_only=True)
    try:
        return {n: con.execute(f"select * from main.{n}").to_arrow_table() for n in MARTS}
    finally:
        con.close()


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """Import, generate, load as seeds and `dbt build`: the first half of the acceptance."""
    root = Path(__file__).resolve().parents[3]
    work = tmp_path_factory.mktemp("jaffle")
    project = work / "jaffle_shop"
    shutil.copytree(
        root / "examples" / "dbt_jaffle_shop",
        project,
        ignore=shutil.ignore_patterns("target", "dbt_packages", "logs", "*.csv"),
    )
    override = os.environ.get("SHAPE_DBT_PACKAGES_FILE")
    if override:
        shutil.copy(override, project / "packages.yml")
    host = default_host()
    gen = work / "jaffle.gen.json"
    assert (
        cli.run_command(host, "from-dbt", [str(project), "--select", "source", "-o", str(gen)]) == 0
    )
    assert (
        cli.run_command(
            host,
            "dbt-seeds",
            [
                str(gen),
                "--project",
                str(project),
                "--rows",
                ROWS,
                "--seed",
                "11",
                "--dialect",
                "duckdb",
                "--metadata",
                str(work / "jaffle.gen.dbt-meta.json"),
            ],
        )  # fmt: skip
        == 0
    )
    dbt(project, "deps")
    build = dbt(project, "build")
    return {"project": project, "work": work, "gen": gen, "build": build}


def test_the_import_reads_the_keys_the_enums_and_the_types(built):
    doc = json.loads(built["gen"].read_text(encoding="utf-8"))
    tables = doc["tables"]
    assert {t: v["primary_key"] for t, v in tables.items()} == {
        "raw_customers": ["customer_id"],
        "raw_orders": ["order_id"],
        "raw_payments": ["payment_id"],
    }
    assert {r["name"] for r in doc["relationships"]} == {
        "fk_raw_orders_customer_id",
        "fk_raw_payments_order_id",
    }
    status = tables["raw_orders"]["columns"]["status"]["generator"]
    assert set(status["values"]) == {"placed", "shipped", "completed", "return_pending", "returned"}
    amount = tables["raw_payments"]["columns"]["amount"]
    assert (amount["type"], amount["precision"], amount["scale"]) == ("decimal", 10, 2)


def test_the_seeds_carry_a_block_of_column_types(built):
    doc = yaml.safe_load(
        (built["project"] / "seeds" / "_shape_seeds.yml").read_text(encoding="utf-8")
    )
    by_name = {s["name"]: s["config"]["column_types"] for s in doc["seeds"]}
    assert by_name["raw_payments"]["amount"] == "decimal(10,2)"
    assert by_name["raw_orders"]["order_date"] == "date"
    assert by_name["raw_customers"]["first_name"] == "varchar"


def test_dbt_build_passes_every_seed_model_and_test(built):
    results = run_results(built["project"])["results"]
    statuses = [r["status"] for r in results]
    assert len(results) == 31  # 3 seeds, 3 views, 2 tables, 23 tests
    assert set(statuses) <= {"success", "pass"}, statuses
    assert statuses.count("pass") == 23
    assert "Completed successfully" in built["build"].stdout


def test_the_generated_data_keeps_the_projects_own_integrity_rules(built):
    tables = marts(built["project"])
    customers, orders = tables["customers"], tables["orders"]
    assert customers.num_rows == 150 and orders.num_rows == 600
    assert set(orders["customer_id"].to_pylist()) <= set(customers["customer_id"].to_pylist())
    assert set(orders["status"].to_pylist()) <= {
        "placed",
        "shipped",
        "completed",
        "return_pending",
        "returned",
    }
    assert str(orders.schema.field("amount").type) == "decimal128(18, 2)"


def test_a_compiled_test_set_round_trips_and_passes_in_dbt(built):
    project = built["project"]
    profile = shape.profile(marts(project), name="marts")
    contract = contract_from_profile(profile)
    compiled = compile_tests(contract, bounds=bounds_from_profile(profile))

    # contract -> dbt tests -> contract: equal for what dbt can express (and, with the meta, all)
    text = compiled.yaml()
    from_tests, bounds = contract_from_schema_yaml(text, use_meta=False)
    assert {"tables": from_tests} == expressible(contract)
    from_meta, _ = contract_from_schema_yaml(text, use_meta=True)
    assert {"tables": from_meta} == normalize_contract(contract)
    assert bounds["orders"]["amount"]["mean"][0] < bounds["orders"]["amount"]["mean"][1]
    assert shape.check(profile, {"tables": from_tests}).passed

    # the tests run inside dbt, with no Python: merged into the project's own schema file
    marts_yml = project / "models" / "marts" / "_marts.yml"
    base = yaml.safe_load(marts_yml.read_text(encoding="utf-8"))
    marts_yml.write_text(
        render_yaml(merge_schema_docs(base, compiled.doc), compiled.packages), encoding="utf-8"
    )
    run = dbt(project, "test", "--select", "tag:shape")
    results = run_results(project)["results"]
    assert len(results) > 40 and {r["status"] for r in results} == {"pass"}
    assert "Completed successfully" in run.stdout


def test_a_failed_dbt_test_and_a_shape_drift_finding_appear_in_one_report(built):
    project, work = built["project"], built["work"]
    baseline = shape.profile(marts(project), name="marts")
    shape.save(baseline, work / "baseline.shape")
    contract = contract_from_profile(baseline)
    (work / "contract.json").write_text(json.dumps(contract), encoding="utf-8")

    con = duckdb.connect(str(project / "target" / "jaffle_shop.duckdb"))
    try:  # the day-2 data: every order is ten times as large
        con.execute("update main.orders set amount = amount * 10")
    finally:
        con.close()
    run = dbt(project, "test", "--select", "tag:shape", check=False)
    assert run.returncode != 0
    current = shape.profile(marts(project), name="marts")
    shape.save(current, work / "current.shape")

    report = work / "report.json"
    code = cli.run_command(
        default_host(),
        "dbt-report",
        [
            "--run-results", str(project / "target" / "run_results.json"),
            "--manifest", str(project / "target" / "manifest.json"),
            "--profile", str(work / "current.shape"),
            "--contract", str(work / "contract.json"),
            "--baseline", str(work / "baseline.shape"),
            "-o", str(report),
            "--md", str(work / "report.md"),
        ],
    )  # fmt: skip
    assert code == 1
    doc = json.loads(report.read_text(encoding="utf-8"))
    assert doc["ok"] is False and doc["summary"]["dbt_failed"] >= 1
    failed = {(f["model"], f["column"], f["test"]) for f in doc["dbt"]["failed"]}
    assert ("orders", "amount", "dbt_utils.accepted_range") in failed
    kinds = {c["kind"] for c in doc["shape"]["drift"]["changes"] if c["column"] == "orders.amount"}
    assert "mean_shift" in kinds
    assert any(v["column"] == "orders.amount" for v in doc["shape"]["contract"]["violations"])
    parts = doc["by_column"]["orders.amount"]
    assert parts["dbt"] and parts["contract"] and parts["drift"]
    text = (work / "report.md").read_text(encoding="utf-8")
    assert "orders.amount" in text and "mean_shift" in text and "accepted_range" in text
