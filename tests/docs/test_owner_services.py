"""Start the authenticated registry with its files ready and tolerate startup resets."""

from __future__ import annotations

import http.client
import subprocess
import sys
import urllib.error
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.URLError("starting"),
        TimeoutError("starting"),
        ConnectionResetError(104, "reset"),
        http.client.RemoteDisconnected("starting"),
    ],
)
def test_readiness_retries_startup_errors_until_both_auth_checks_pass(monkeypatch, error):
    import docs_owner_services as services

    authenticated = Mock()
    authenticated.__enter__ = Mock(return_value=authenticated)
    authenticated.__exit__ = Mock(return_value=False)
    authenticated.status = 200
    unauthorized = urllib.error.HTTPError(
        "http://localhost:8081/subjects", 401, "Unauthorized", {}, None
    )
    request = Mock(side_effect=[error, authenticated, unauthorized])
    monkeypatch.setattr(services.urllib.request, "urlopen", request)
    sleep = Mock()
    monkeypatch.setattr(services.time, "sleep", sleep)
    services.wait_ready("real-started-container", "private-password", timeout=120)
    assert request.call_count == 3
    assert sleep.call_count == 1


def test_readiness_rejects_anonymous_success(monkeypatch):
    import docs_owner_services as services

    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.status = 200
    monkeypatch.setattr(services.urllib.request, "urlopen", Mock(return_value=response))
    with pytest.raises(RuntimeError, match="unauthenticated"):
        services.wait_ready("container", "private-password")


def test_timeout_prints_last_200_log_lines_from_the_started_container(monkeypatch, capsys):
    import docs_owner_services as services

    monkeypatch.setattr(services.time, "monotonic", Mock(side_effect=[0, 0, 121, 121]))
    monkeypatch.setattr(services.time, "sleep", Mock())
    monkeypatch.setattr(
        services.urllib.request, "urlopen", Mock(side_effect=ConnectionError("starting"))
    )
    run = Mock(return_value=subprocess.CompletedProcess([], 0, "startup error private-password\n"))
    monkeypatch.setattr(services.subprocess, "run", run)
    with pytest.raises(TimeoutError):
        services.wait_ready("actual-started-id", "private-password", timeout=120)
    assert run.call_args.args[0] == ["docker", "logs", "--tail", "200", "actual-started-id"]
    output = capsys.readouterr()
    assert "startup error" in output.err
    assert "private-password" not in output.err


def test_run_mounts_prepared_files_on_kafkas_network_and_returns_actual_id(tmp_path, monkeypatch):
    import docs_owner_services as services

    services.write_auth(tmp_path, "private-password")
    assert "svc: private-password,docs" in (tmp_path / "docs-users.properties").read_text()
    assert (
        'file="/run/shape-registry/docs-users.properties"'
        in (tmp_path / "docs-jaas.conf").read_text()
    )
    run = Mock(return_value=subprocess.CompletedProcess([], 0, "a" * 64 + "\n"))
    monkeypatch.setattr(services.subprocess, "run", run)
    container = services.start_registry(tmp_path, "job-kafka-network", "123")
    assert container == "a" * 64
    command = run.call_args.args[0]
    assert command[:3] == ["docker", "run", "-d"]
    assert command[command.index("--network") + 1] == "job-kafka-network"
    assert command[command.index("--publish") + 1] == "127.0.0.1:8081:8081"
    assert any(f"src={tmp_path}" in argument and "readonly" in argument for argument in command)
    assert "private-password" not in " ".join(command)
    assert "SCHEMA_REGISTRY_AUTHENTICATION_METHOD=BASIC" in command
    assert "SCHEMA_REGISTRY_AUTHENTICATION_REALM=ShapeDocs" in command
    assert "SCHEMA_REGISTRY_AUTHENTICATION_ROLES=docs" in command


def test_workflow_starts_registry_in_step_on_kafkas_service_network():
    workflow = yaml.safe_load((ROOT / ".github/workflows/docs-transcripts.yml").read_text())
    job = workflow["jobs"]["capture"]
    assert "schema-registry" not in job["services"]
    step = next(
        step for step in job["steps"] if step.get("name") == "Require authenticated Schema Registry"
    )
    assert step["env"]["SHAPE_DOCS_DOCKER_NETWORK"] == "${{ job.services.kafka.network }}"
    assert "SCHEMA_REGISTRY_CONTAINER" not in step["env"]


def test_authenticated_non_200_response_never_counts_as_ready(monkeypatch):
    import docs_owner_services as services

    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.status = 204
    request = Mock(return_value=response)
    monkeypatch.setattr(services.urllib.request, "urlopen", request)
    with pytest.raises(RuntimeError, match="authenticated request did not return 200"):
        services.wait_ready("container", "private-password")
    assert request.call_count == 1


def test_main_writes_files_before_start_and_keeps_them_for_later_logins(tmp_path, monkeypatch):
    import docs_owner_services as services

    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("GITHUB_ENV", str(tmp_path / "job-env"))
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("SHAPE_DOCS_DOCKER_NETWORK", "kafka-service-network")
    monkeypatch.setattr(services.secrets, "token_urlsafe", Mock(return_value="private-password"))
    prepared = []

    def start(directory, network, run_id):
        assert network == "kafka-service-network" and run_id == "123"
        assert (directory / "docs-jaas.conf").exists()
        assert (directory / "docs-users.properties").exists()
        assert directory.parent.stat().st_mode & 0o777 == 0o700
        prepared.append(directory)
        return "a" * 64

    monkeypatch.setattr(services, "start_registry", start)
    ready = Mock()
    monkeypatch.setattr(services, "wait_ready", ready)
    services.main()
    ready.assert_called_once_with("a" * 64, "private-password")
    assert (prepared[0] / "docs-users.properties").exists()
    assert "SCHEMA_REGISTRY_CONTAINER=" + "a" * 64 in (tmp_path / "job-env").read_text()
