"""The built-in modules are ``shape.behaviors`` plugins and pass the conformance kit."""

import json
import subprocess
import sys

import pytest
from shape_behavior.behaviors import ModuleBehavior, behavior

from shape.plugins import kit
from shape.plugins.host import PluginHost

BUILTIN = ("subscription", "equipment_maintenance", "healthcare_screening")


@pytest.mark.parametrize("name", BUILTIN)
def test_builtin_is_registered_loads_and_conforms(name):
    host = PluginHost()
    rec = host.record("shape.behaviors", name)
    assert rec is not None and rec.source == "sqllocks-shape-behavior"
    obj = host.get("shape.behaviors", name)
    assert obj.name == name
    kit.check_behavior(obj)
    kit.check_plugin("shape.behaviors", obj)


def test_distribution_passes_the_kit_from_the_command_line():
    r = subprocess.run(
        [sys.executable, "-m", "shape.plugins.kit", "sqllocks-shape-behavior"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    for name in BUILTIN:
        assert f"shape.behaviors:{name}: ok\n" in r.stdout
    assert "shared rules only" not in r.stdout.split("shape.commands")[0]


def test_shape_plugins_list_shows_the_behavior_modules():
    r = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from shape.cli.main import main; sys.exit(main())",
            "plugins",
            "list",
            "--json",
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    rows = {(x["group"], x["name"]): x for x in json.loads(r.stdout)["payload"]}  # W1-14
    for name in BUILTIN:
        assert rows[("shape.behaviors", name)]["source"] == "sqllocks-shape-behavior"


def test_protocol_attributes_describe_the_module():
    b = host_get("subscription")
    assert b.version
    assert {"start", "trial_started"} <= set(b.states)
    assert {"plan", "payments"} <= set(b.attributes)
    assert {"trial_started", "invoice_paid", "subscription_cancelled"} <= set(b.events)


def host_get(name):
    return PluginHost().get("shape.behaviors", name)


def test_two_non_healthcare_examples_and_one_healthcare_example():
    assert {"subscription", "equipment_maintenance"} <= set(BUILTIN)
    clinical = host_get("healthcare_screening")
    assert {"encounter", "condition_onset", "medication_order", "observation"} <= set(
        clinical.events
    )


def test_behavior_helper_wraps_any_document():
    doc = {
        "format": "shape-behavior/1",
        "name": "tiny",
        "states": {
            "s": {"type": "initial", "transition": {"direct": "e"}},
            "e": {"type": "event", "event": "ping", "transition": {"direct": "end"}},
            "end": {"type": "terminal"},
        },
    }
    b = behavior(doc, version="2.1")
    assert isinstance(b, ModuleBehavior) and b.version == "2.1" and b.events == ["ping"]
    kit.check_behavior(b)
    assert b.simulate(10, 1, 1).num_rows == 10


def test_load_module_finds_a_registered_behavior_by_name():
    from shape_behavior import load_module

    assert load_module("subscription").name == "subscription"
