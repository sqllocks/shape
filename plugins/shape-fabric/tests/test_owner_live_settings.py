"""The documented unattended Synapse settings reach its credential factory."""

import importlib.util
from pathlib import Path

import pytest


def test_synapse_live_service_principal_settings_are_used(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "owner_live_synapse", Path(__file__).with_name("test_live_synapse.py")
    )
    assert spec is not None and spec.loader is not None
    live = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(live)
    for name, value in {
        "AUTH": "spn",
        "TENANT_ID": "test-tenant",
        "CLIENT_ID": "test-client",
        "CLIENT_SECRET": "test-secret",
    }.items():
        monkeypatch.setenv("SHAPE_TEST_SYNAPSE_" + name, value)
    seen = []
    identity = object()

    def build(settings):
        seen.append(settings)
        return identity

    monkeypatch.setattr(live, "build_credential", build)
    assert live.credential() is identity
    assert seen[0].tenant_id == "test-tenant"
    assert seen[0].client_id == "test-client"
    assert seen[0].client_secret == "env://SHAPE_TEST_SYNAPSE_CLIENT_SECRET"


@pytest.mark.parametrize(
    "filename",
    [
        "test_live.py",
        "test_live_commands.py",
        "test_live_git_sync.py",
        "test_live_owner_matrix.py",
        "test_live_scale_sinks.py",
        "test_live_semantic_model.py",
        "test_live_synapse.py",
        "test_live_writers.py",
    ],
)
def test_live_missing_variable_fails_by_name(filename, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "owner_live_missing_setting", Path(__file__).with_name(filename)
    )
    assert spec is not None and spec.loader is not None
    live = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(live)
    name = "SHAPE_TEST_MISSING_LIVE_VARIABLE"
    monkeypatch.delenv(name, raising=False)
    try:
        live.need(name)
    except BaseException as exc:
        assert isinstance(exc, pytest.fail.Exception), type(exc).__name__
        assert name in str(exc)
    else:
        pytest.fail("missing live variable did not fail")
