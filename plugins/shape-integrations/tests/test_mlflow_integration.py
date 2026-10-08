"""Item 3 with MLflow installed: a real tracking store (SQLite) in a temporary directory."""

from __future__ import annotations

import json

import pytest
from shape_integrations.testing import REPRO, write_manifest

pytest.importorskip("mlflow", reason="needs the 'mlflow' extra")
pytestmark = pytest.mark.integration

REPORT = {
    "shape_version": "0.9.0",
    "passed": False,
    "gates": [
        {"gate": "schema_conformance", "passed": True, "errors": [], "warnings": [], "details": {}},
        {
            "gate": "referential_integrity",
            "passed": False,
            "errors": ["orders.customer_id has 1 orphan"],
            "warnings": [],
            "details": {"orphans": 1},
        },
    ],
}


def run(*argv: str) -> int:
    from shape.plugins.cli import run_command
    from shape.plugins.host import default_host

    return run_command(default_host(), "mlflow", list(argv))


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A tracking URI of a fresh SQLite store; artifacts land under the temporary directory."""
    monkeypatch.chdir(tmp_path)
    return f"sqlite:///{tmp_path / 'mlflow.db'}"


def runs_of(store: str, experiment: str):
    import mlflow
    from mlflow.tracking import MlflowClient

    client = MlflowClient(tracking_uri=store)
    exp = client.get_experiment_by_name(experiment)
    assert exp is not None
    found = mlflow.search_runs([exp.experiment_id], output_format="list")
    return client, found


def test_one_run_with_params_metrics_artifacts_and_tags(store, manifest, tmp_path):
    profile = tmp_path / "p.shape"
    profile.write_text("{}")
    report = tmp_path / "r.json"
    report.write_text(json.dumps(REPORT))
    code = run(
        "log",
        str(manifest),
        "--profile",
        str(profile),
        "--verify-report",
        str(report),
        "--experiment",
        "exp1",
        "--tracking-uri",
        store,
    )
    assert code == 0
    client, found = runs_of(store, "exp1")
    assert len(found) == 1
    r = found[0]
    assert r.data.tags["shape.run_id"] == "20261003_120000_retail_small_s42"
    assert r.data.tags["shape.dataset_id"].startswith("sha256:")
    assert {k: v for k, v in r.data.params.items()} == {k: str(v) for k, v in REPRO.items()}
    assert r.data.metrics["gate.schema_conformance.passed"] == 1.0
    assert r.data.metrics["gate.referential_integrity.passed"] == 0.0
    assert r.data.metrics["gate.referential_integrity.orphans"] == 1.0
    assert r.data.metrics["passed"] == 0.0
    names = {a.path for a in client.list_artifacts(r.info.run_id)}
    assert names == {"manifest.json", "p.shape", "r.json"}
    assert r.info.status == "FINISHED"


def test_a_manifest_alone_logs_params_tags_and_the_manifest(store, manifest):
    assert run("log", str(manifest), "--tracking-uri", store, "--experiment", "e") == 0
    client, found = runs_of(store, "e")
    (r,) = found
    assert not r.data.metrics
    assert {a.path for a in client.list_artifacts(r.info.run_id)} == {"manifest.json"}


def test_a_manifest_without_tuple_or_dataset_id_logs_only_the_run_id_tag(store, tmp_path):
    m = write_manifest(tmp_path / "old", with_repro=False)
    assert run("log", str(m), "--tracking-uri", store, "--experiment", "e") == 0
    _, found = runs_of(store, "e")
    (r,) = found
    assert not r.data.params
    assert "shape.dataset_id" not in r.data.tags
    assert r.data.tags["shape.run_id"]


def test_a_second_log_of_the_same_run_is_refused(store, manifest, capsys):
    args = ["log", str(manifest), "--tracking-uri", store, "--experiment", "e"]
    assert run(*args) == 0
    capsys.readouterr()
    assert run(*args) == 1
    err = capsys.readouterr().err
    assert "20261003_120000_retail_small_s42" in err and "--allow-duplicate" in err
    _, found = runs_of(store, "e")
    assert len(found) == 1


def test_allow_duplicate_logs_it_again(store, manifest):
    args = ["log", str(manifest), "--tracking-uri", store, "--experiment", "e"]
    assert run(*args) == 0
    assert run(*args, "--allow-duplicate") == 0
    _, found = runs_of(store, "e")
    assert len(found) == 2


def test_the_same_run_in_another_experiment_is_not_a_duplicate(store, manifest):
    base = ["log", str(manifest), "--tracking-uri", store]
    assert run(*base, "--experiment", "one") == 0
    assert run(*base, "--experiment", "two") == 0


def test_a_run_id_with_quotes_does_not_break_the_duplicate_check(store, tmp_path):
    m = write_manifest(tmp_path / "q", run_id="run'\"x")
    args = ["log", str(m), "--tracking-uri", store, "--experiment", "e"]
    assert run(*args) == 0
    assert run(*args) == 1


def test_the_default_experiment_is_named_shape(store, manifest):
    assert run("log", str(manifest), "--tracking-uri", store) == 0
    _, found = runs_of(store, "shape")
    assert len(found) == 1


def test_a_store_that_cannot_be_reached_exits_1(manifest, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "0")
    monkeypatch.setenv("MLFLOW_HTTP_REQUEST_TIMEOUT", "2")
    code = run("log", str(manifest), "--tracking-uri", "http://127.0.0.1:1")
    assert code == 1
    assert "MLflow" in capsys.readouterr().err
