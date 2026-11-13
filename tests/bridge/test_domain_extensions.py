"""Installed domain entry points extend the catalog without changing the built-in baseline."""

from __future__ import annotations

from importlib.metadata import EntryPoint, distribution

import pytest
from bridge_helpers import Caller
from test_catalog import cli_json
from test_scale import wait_final

from shape.generation.domains import domain_description, domain_names, load_domain
from shape.plugins import host
from shape.plugins.host import PluginHost
from shape.plugins.registry import register_builtins

EXTENSIONS = (
    EntryPoint("fixture_alpha", "domain_fixtures:WithDescription", "shape.domains"),
    EntryPoint("fixture_beta", "domain_fixtures:WithoutDescription", "shape.domains"),
)


def test_domain_entry_points_are_additive(
    api: Caller,
    builtin_domain_host: PluginHost,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    baseline = api.ok("list")
    before = {row["name"]: row for row in baseline["domains"]}
    descriptions = {name: api.ok("describe", domain=name) for name in before}
    entries = tuple(distribution("sqllocks-shape-domains").entry_points)
    extended = PluginHost(entry_points=lambda: (*entries, *EXTENSIONS))
    register_builtins(extended)
    monkeypatch.setattr(host, "_default", extended)

    result = api.ok("list")
    after = {row["name"]: row for row in result["domains"]}
    assert set(after) == set(before) | {ep.name for ep in EXTENSIONS}
    assert result["count"] == baseline["count"] + len(EXTENSIONS)
    assert {name: after[name] for name in before} == before
    assert {name: api.ok("describe", domain=name) for name in before} == descriptions
    assert set(domain_names()) == set(after)
    cli = cli_json(capsys, "list", "--json")["payload"]
    assert {row["name"] for row in cli} == set(after)

    for ep in EXTENSIONS:
        loaded = load_domain(ep.name)
        expected = domain_description(ep.name) or loaded.schema.model.description
        assert after[ep.name]["description"] == expected
        described = api.ok("describe", domain=ep.name)
        cli_description = cli_json(capsys, "describe", ep.name, "--json")
        assert (
            described["description"]
            == cli_description["description"]
            == (loaded.schema.model.description)
        )
        assert described["table_count"] == 1
        started = api.ok("stream", domain=ep.name, max_chunks=1, interval_seconds=0, chunk_size=2)
        state = wait_final(api, started["stream_id"])
        assert state["status"] == "succeeded", state
        assert state["result"]["chunks_written"] == 1
        assert state["result"]["rows_written"] == 2

    assert domain_description("fixture_beta") is None
    assert after["fixture_beta"]["description"] == "Fixture schema description"
    assert after["fixture_alpha"]["description"] == "Generic extension domain"
