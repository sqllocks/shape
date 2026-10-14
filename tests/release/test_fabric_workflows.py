"""The shared Fabric workspace is serialized and its identifiers are secrets."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "fabric_workflow_checks", ROOT / "scripts" / "check_release_workflows.py"
)
assert _spec and _spec.loader
checks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checks)

FABRIC_IDS = (
    "FABRIC_TENANT_ID",
    "FABRIC_CLIENT_ID",
    "FABRIC_WORKSPACE_ID",
    "FABRIC_LAKEHOUSE_ID",
    "FABRIC_SQL_ENDPOINT",
    "FABRIC_CLASSIC_LAKEHOUSE_TABLES_PATH",
    "FABRIC_SCHEMA_LAKEHOUSE_TABLES_PATH",
    "FABRIC_WAREHOUSE",
    "FABRIC_EVENTHOUSE_URI",
    "FABRIC_KQL_DB",
    "FABRIC_SAMPLE_WAREHOUSE_CONNECTION_STRING",
    "FABRIC_SEMANTIC_MODEL",
    "FABRIC_GIT_WORKSPACE_ID",
    "FABRIC_GIT_REMOTE",
)
NIGHTLY_JOBS = ("fabric-live", "fabric-git-sync-live", "fabric-emit-e2e", "emit-live", "abfss-live")


def _workflows(tmp_path: Path) -> Path:
    directory = tmp_path / "workflows"
    shutil.copytree(ROOT / ".github" / "workflows", directory)
    return directory


def _mutate(directory: Path, name: str, change: Any) -> None:
    path = directory / name
    workflow = yaml.safe_load(path.read_text())
    change(workflow)
    path.write_text(yaml.safe_dump(workflow, sort_keys=False))


def test_fabric_workspace_and_identifiers_are_guarded() -> None:
    assert checks.check_fabric_workflows(ROOT / ".github" / "workflows") == []


@pytest.mark.parametrize("name", FABRIC_IDS)
def test_each_identifier_must_stay_an_environment_secret(tmp_path: Path, name: str) -> None:
    directory = _workflows(tmp_path)
    _mutate(
        directory,
        "fabric-live.yml",
        lambda wf: wf["jobs"]["fabric-live"]["env"].update({name: f"${{{{ vars.{name} }}}}"}),
    )
    assert any(name in problem for problem in checks.check_fabric_workflows(directory))


@pytest.mark.parametrize("job", NIGHTLY_JOBS)
@pytest.mark.parametrize(
    "lock",
    [
        None,
        {"group": "other", "cancel-in-progress": False},
        {"group": "fabric-workspace", "cancel-in-progress": True},
    ],
)
def test_every_nightly_workspace_job_requires_the_shared_lock(
    tmp_path: Path, job: str, lock: Any
) -> None:
    directory = _workflows(tmp_path)
    _mutate(directory, "nightly.yml", lambda wf: wf["jobs"][job].update(concurrency=lock))
    assert any(job in problem for problem in checks.check_fabric_workflows(directory))


def test_new_jobs_using_fabric_credentials_need_the_lock(tmp_path: Path) -> None:
    directory = _workflows(tmp_path)
    _mutate(
        directory,
        "nightly.yml",
        lambda wf: wf["jobs"].update(
            new_workspace_job={"env": {"WORKSPACE": "${{ secrets.FABRIC_WORKSPACE_ID }}"}}
        ),
    )
    assert any("new_workspace_job" in p for p in checks.check_fabric_workflows(directory))


def test_workflow_lock_cannot_be_overridden_by_a_fabric_job(tmp_path: Path) -> None:
    directory = _workflows(tmp_path)
    _mutate(
        directory,
        "fabric-live.yml",
        lambda wf: wf["jobs"]["seed"].update(concurrency={"group": "other"}),
    )
    assert any("seed" in problem for problem in checks.check_fabric_workflows(directory))


def test_environment_secret_inputs_require_the_fabric_environment(tmp_path: Path) -> None:
    directory = _workflows(tmp_path)
    _mutate(directory, "fabric-live.yml", lambda wf: wf["jobs"]["seed"].pop("environment"))
    assert any("environment" in problem for problem in checks.check_fabric_workflows(directory))


def test_cli_enforces_fabric_policy(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    directory = _workflows(tmp_path)
    _mutate(directory, "nightly.yml", lambda wf: wf["jobs"]["emit-live"].pop("concurrency"))
    assert checks.main(["--dir", str(directory)]) == 1
    assert "emit-live" in capsys.readouterr().err


def test_new_jobs_with_a_named_fabric_environment_need_the_lock(tmp_path: Path) -> None:
    directory = _workflows(tmp_path)
    _mutate(
        directory,
        "nightly.yml",
        lambda wf: wf["jobs"].update(new_environment_job={"environment": {"name": "fabric-live"}}),
    )
    assert any("new_environment_job" in p for p in checks.check_fabric_workflows(directory))
