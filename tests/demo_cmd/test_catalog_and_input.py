"""P6-12: the scenarios, and what each command does with input it cannot use."""

from __future__ import annotations

import json

import pytest

from shape.demo.api import demo_list, params_from
from shape.demo.catalog import ScenarioCatalog, ScenarioMeta, get_catalog
from shape.demo.errors import DemoError

EXPECTED = {
    "retail": (["inference", "streaming", "seeding"], ["retail"], 100_000),
    "adventureworks": (["inference", "seeding"], ["retail"], 50_000),
    "healthcare": (["inference", "streaming", "seeding"], ["healthcare"], 50_000),
    "enterprise": (["seeding"], ["retail", "hr", "financial"], 200_000),
}


def test_the_four_scenarios_have_their_modes_domains_and_default_rows():
    found = {s.name: (s.supported_modes, s.domains, s.default_rows) for s in get_catalog().list()}
    assert found == EXPECTED


def test_demo_list_has_the_payload_the_bridge_returns(run):
    code, out, _ = run("demo", "list", "--json")
    assert code == 0
    envelope = json.loads(out)
    assert envelope["format"] == "shape-result" and envelope["command"] == "demo list"
    payload = {
        k: v for k, v in envelope.items() if k not in ("format", "version", "command", "exit_code")
    }
    assert payload == demo_list()
    assert payload["count"] == 4
    assert set(payload["scenarios"][0]) == {
        "name", "description", "supported_modes", "domains", "default_rows", "tags",
    }  # fmt: skip


def test_demo_list_prints_one_line_per_scenario(run):
    code, out, _ = run("demo", "list")
    assert code == 0
    for name in EXPECTED:
        assert any(line.startswith(name) for line in out.splitlines())


def test_compose_names_a_dynamic_scenario():
    meta = ScenarioCatalog().compose(["retail", "hr"], "seeding")
    assert (meta.name, meta.domains, meta.supported_modes) == (
        "custom_retail_hr", ["retail", "hr"], ["seeding"],
    )  # fmt: skip
    assert isinstance(meta, ScenarioMeta) and meta.tags == ["dynamic", "custom"]


def test_an_unknown_scenario_is_a_one_line_error(run):
    code, out, err = run("demo", "run", "nope")
    assert code == 2 and out == ""
    assert "scenario 'nope' not found" in err and "Traceback" not in err


def test_a_mode_the_scenario_does_not_support_is_refused(run):
    code, _, err = run("demo", "run", "enterprise", "--mode", "inference")
    assert code == 2 and "does not support mode 'inference'" in err


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--rows", "0"], "rows must be at least 1"),
        (["--rows", "-5"], "rows must be at least 1"),
        (["--output", "terminal,chart"], "unknown output format 'chart'"),
        (["--max-events", "0"], "max_events must be at least 1"),
        (["--connection", "ghost"], "no connection profile 'ghost'"),
    ],
)
def test_unusable_settings_are_refused_before_anything_runs(run, home, args, message):
    code, _, err = run("demo", "run", "retail", *args)
    assert code == 2 and message in err
    assert not (home / "sessions").exists()  # no session was made


def test_params_from_checks_names_and_takes_the_scenarios_rows():
    assert params_from({"scenario": "healthcare"}).rows == 50_000
    assert params_from({"rows": "300", "domains": "a, b"}).domains == ["a", "b"]
    with pytest.raises(DemoError, match="unknown demo setting"):
        params_from({"rowz": 5})
    with pytest.raises(DemoError, match="rows must be a whole number"):
        params_from({"rows": "many"})
