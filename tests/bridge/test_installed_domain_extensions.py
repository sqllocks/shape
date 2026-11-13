"""Normal installed discovery remains additive outside frozen baseline replays."""

from __future__ import annotations

from pathlib import Path

import pytest
from bridge_helpers import Caller

from shape.bridge.core import Bridge
from shape.generation.domains import domain_names
from shape.plugins import host
from shape.plugins.host import PluginHost

EXTENSION = "fixture_alpha"
DISTRIBUTION = "shape-fixture-extension"


@pytest.fixture
def installed_domain_extension(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    before = Caller(Bridge(tmp_path / "before-jobs")).ok("list")
    site = tmp_path / "site"
    metadata = site / "shape_fixture_extension-0.0.0.dist-info"
    metadata.mkdir(parents=True)
    (metadata / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {DISTRIBUTION}\nVersion: 0.0.0\n"
    )
    (metadata / "entry_points.txt").write_text(
        f"[shape.domains]\n{EXTENSION} = domain_fixtures:WithDescription\n"
    )
    monkeypatch.syspath_prepend(str(site))
    monkeypatch.setattr(host, "_default", None)
    discovered = host.default_host()
    yield before, discovered
    # A dependent baseline replay fixture has already finished at this point.
    assert host.default_host() is discovered
    assert EXTENSION in domain_names()


def test_installed_extension_uses_normal_discovery(api: Caller, installed_domain_extension):
    before, discovered = installed_domain_extension
    previous = {row["name"]: row for row in before["domains"]}
    result = api.ok("list")
    current = {row["name"]: row for row in result["domains"]}
    assert set(current) == set(previous) | {EXTENSION}
    assert result["count"] == before["count"] + 1
    assert {name: current[name] for name in previous} == previous
    assert current[EXTENSION]["description"] == "Generic extension domain"
    record = discovered.record("shape.domains", EXTENSION)
    assert record is not None and record.source == DISTRIBUTION
    described = api.ok("describe", domain=EXTENSION)
    assert described["name"] == EXTENSION
    assert described["description"] == "Fixture schema description"
    assert described["table_count"] == 1
    error = api.fail("describe", "input.unknown_domain", domain="fixture_missing")
    known = ", ".join(sorted(current))
    assert error["message"] == (
        f"no domain named 'fixture_missing' (installed: {known}); domains come from the "
        "'sqllocks-shape-domains' package"
    )


@pytest.mark.usefixtures("installed_domain_extension")
def test_builtin_replay_scope_restores_installed_extension(
    api: Caller, builtin_domain_host: PluginHost
):
    assert host.default_host() is builtin_domain_host
    assert EXTENSION not in {row["name"] for row in api.ok("list")["domains"]}
    api.fail("describe", "input.unknown_domain", domain=EXTENSION)
    # installed_domain_extension's finalizer verifies discovery after this fixture ends.
