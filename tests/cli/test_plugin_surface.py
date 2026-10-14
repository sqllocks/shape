"""W-plug: listed plugin surfaces, ownership, aliases and exit classes."""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import sys
import tomllib
from pathlib import Path

import pytest

from shape.cli import stability
from shape.cli.main import _build_parser, main
from shape.plugins import cli
from shape.plugins.host import PluginBlockedError, PluginHost

ROOT = Path(__file__).resolve().parents[2]
DIST = "sqllocks-shape-fabric"
BASELINE = ROOT / "tests/cli/cli_surface_plugins_v1.json"


def _script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"scripts/{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


surface = _script("cli_surface")
versions = _script("check_versions")
generator = _script("gen_exit_codes")


class Publish:
    name = "publish"
    help = "publish a domain"

    def configure(self, parser):
        parser.add_argument("DOMAIN")
        parser.add_argument("--target", choices=["lakehouse", "warehouse"])
        parser.add_argument("--fail", action="store_true")

    def run(self, args):
        if args.DOMAIN == "bad":
            raise ValueError("bad\ninput")
        if args.DOMAIN == "bug":
            raise RuntimeError("a bug")
        return 1 if args.fail else 0


class Setup(Publish):
    name = "setup-fabric"
    help = "set up Fabric"


class Fabric:
    name = "fabric"
    help = "Fabric commands"

    def configure(self, parser):
        sub = parser.add_subparsers(dest="fabric_command", required=True)
        for name, command in [("publish", Publish()), ("setup", Setup())]:
            child = sub.add_parser(name, help=command.help, description=command.help)
            command.configure(child)
            child.set_defaults(run_child=command.run)

    def run(self, args):
        return args.run_child(args)


@pytest.fixture
def host():
    value = PluginHost(entry_points=lambda: [])
    value.register("shape.commands", "publish", Publish, source=DIST)
    value.register("shape.commands", "setup-fabric", Setup, source=DIST)
    value.register("shape.commands", "fabric", Fabric, source=DIST)
    return value


@pytest.fixture
def listed(monkeypatch):
    # Temporary promotions exercise the mechanism; no production command is promoted here.
    monkeypatch.setattr(stability, "STABLE_PLUGIN_COMMANDS", {"publish": DIST}, raising=False)


def _one_error(result, captured):
    assert result == 2
    assert captured.out == ""
    assert captured.err.startswith("shape: error: ")
    assert len(captured.err.splitlines()) == 1
    assert "Traceback" not in captured.err


def test_plugin_registry_and_live_baseline():
    assert isinstance(stability.STABLE_PLUGIN_COMMANDS, dict)
    assert surface.snapshot_plugins() == json.loads(BASELINE.read_text())
    assert surface.main(["--check", "--plugins"]) == 0


def test_listed_names_and_fabric_aliases_have_the_same_level(listed):
    assert stability.level("publish") == "stable"
    assert stability.level("fabric publish") == "stable"
    assert stability.level("fabric") == "experimental"
    assert stability.level("setup-fabric") == "experimental"


def test_stubs_and_loaded_help_mark_experimental_commands(host, capsys):
    sub = next(
        a
        for a in _build_parser(host.records("shape.commands"))._actions
        if isinstance(a, argparse._SubParsersAction)
    )
    helps = {a.dest: a.help for a in sub._choices_actions}
    for name in ("publish", "setup-fabric", "fabric"):
        assert helps[name] == f"(plugin {DIST}, experimental)"
        assert cli.run_command(host, name, ["--help"]) == 0
        assert "(experimental)" in capsys.readouterr().out


def test_listed_help_has_no_marker_and_only_its_fabric_alias_is_stable(listed, host, capsys):
    parser = _build_parser(host.records("shape.commands"))
    assert f"(plugin {DIST})" in parser.format_help()
    assert cli.run_command(host, "publish", ["--help"]) == 0
    assert "(experimental)" not in capsys.readouterr().out
    assert cli.run_command(host, "fabric", ["publish", "--help"]) == 0
    assert "(experimental)" not in capsys.readouterr().out
    assert cli.run_command(host, "fabric", ["setup", "--help"]) == 0
    assert "(experimental)" in capsys.readouterr().out


def test_loader_refuses_another_distribution_before_calling_its_factory(listed):
    called = []

    def factory():
        called.append(True)
        return Publish()

    host = PluginHost(entry_points=lambda: [])
    host.register("shape.commands", "publish", factory, source="third-party")
    with pytest.raises(PluginBlockedError, match=DIST):
        host.get("shape.commands", "publish")
    assert called == []


def test_alias_group_cannot_come_from_another_distribution(listed):
    host = PluginHost(entry_points=lambda: [])
    host.register("shape.commands", "fabric", Fabric, source="third-party")
    with pytest.raises(PluginBlockedError, match=DIST):
        host.get("shape.commands", "fabric")


def test_snapshot_loads_both_fabric_spellings_and_setup_rename(listed, host, monkeypatch):
    monkeypatch.setitem(stability.STABLE_PLUGIN_COMMANDS, "setup-fabric", DIST)
    live = surface.snapshot_plugins(host)
    commands = live[DIST]["commands"]
    assert set(commands) == {"publish", "fabric publish", "setup-fabric", "fabric setup"}
    assert commands["publish"] == commands["fabric publish"]
    assert commands["setup-fabric"] == commands["fabric setup"]
    assert commands["publish"]["stability"] == "stable"


@pytest.mark.parametrize("path", ["publish", "fabric publish"])
@pytest.mark.parametrize("change", ["flag", "choice", "value", "positional", "command", "demotion"])
def test_each_spelling_rejects_breaking_changes(listed, host, path, change):
    baseline = surface.snapshot_plugins(host)
    live = copy.deepcopy(baseline)
    command = live[DIST]["commands"][path]
    if change == "flag":
        command["flags"].pop("--target")
    elif change == "choice":
        command["flags"]["--target"]["choices"].pop()
    elif change == "value":
        command["flags"]["--target"]["takes_value"] = False
    elif change == "positional":
        command["positionals"].pop()
    elif change == "command":
        live[DIST]["commands"].pop(path)
    else:
        command["stability"] = "experimental"
    problems, _ = surface.compare(baseline, live)
    assert problems and any(path in p for p in problems)


def test_distribution_removal_or_transfer_breaks(listed, host):
    baseline = surface.snapshot_plugins(host)
    removed = copy.deepcopy(baseline)
    removed.pop(DIST)
    assert surface.compare(baseline, removed)[0]
    transferred = copy.deepcopy(removed)
    transferred["sqllocks-shape-sqlserver"] = baseline[DIST]
    assert surface.compare(baseline, transferred)[0]


def test_plugin_writer_accepts_additions_and_refuses_breaks(listed, host, monkeypatch, tmp_path):
    surface_for_test = surface.snapshot_plugins(host)
    monkeypatch.setattr(surface, "snapshot_plugins", lambda: surface_for_test)
    target = tmp_path / "plugins.json"
    argv = ["--write", "--plugins", "--baseline", str(target)]
    assert surface.main(argv) == 0
    original = target.read_text()
    surface_for_test[DIST]["commands"]["publish"]["flags"]["--new"] = {
        "takes_value": False,
        "choices": None,
    }
    assert surface.main(argv) == 0
    original = target.read_text()
    surface_for_test[DIST]["commands"]["publish"]["flags"].pop("--target")
    assert surface.main(argv) == 1
    assert target.read_text() == original


def test_a_missing_listed_plugin_fails_snapshot_and_check(listed, monkeypatch, capsys):
    empty = PluginHost(entry_points=lambda: [])
    with pytest.raises(ValueError, match=DIST):
        surface.snapshot_plugins(empty)
    monkeypatch.setattr("shape.plugins.host.PluginHost", lambda: empty)
    assert surface.main(["--check", "--plugins"]) == 2
    assert DIST in capsys.readouterr().err


@pytest.mark.parametrize("argv", [["publish"], ["fabric", "publish"]])
def test_missing_listed_plugin_is_one_line_exit_2(listed, monkeypatch, tmp_path, capsys, argv):
    monkeypatch.chdir(tmp_path)
    empty = PluginHost(entry_points=lambda: [])
    monkeypatch.setattr("shape.plugins.host.default_host", lambda: empty)
    _one_error(main(argv), capsys.readouterr())
    # Exercise the direct plugin dispatcher as well as the public CLI.
    assert cli.run_command(empty, argv[0], argv[1:]) == 2
    assert DIST in capsys.readouterr().err


@pytest.mark.parametrize(
    "name,argv",
    [
        ("publish", []),
        ("publish", ["bad"]),
        ("fabric", ["publish"]),
        ("fabric", ["publish", "bad"]),
    ],
)
def test_listed_bad_inputs_are_one_line_exit_2(listed, host, capsys, name, argv):
    _one_error(cli.run_command(host, name, argv), capsys.readouterr())


def test_listed_success_verdict_and_unexpected_bug_keep_their_classes(listed, host, capsys):
    assert cli.run_command(host, "publish", ["ok"]) == 0
    assert cli.run_command(host, "publish", ["ok", "--fail"]) == 1
    assert cli.run_command(host, "publish", ["bug"]) == 1
    assert "RuntimeError" in capsys.readouterr().err


def test_every_listed_command_and_alias_has_a_bad_input_exit_class(capsys):
    # A real installed command is mandatory when listed; no skip for an absent plugin.
    live = surface.snapshot_plugins()
    host = PluginHost()
    for distribution in set(stability.STABLE_PLUGIN_COMMANDS.values()):
        for path in live[distribution]["commands"]:
            name, *args = path.split()
            _one_error(
                cli.run_command(host, name, [*args, "--W-plug-invalid"]), capsys.readouterr()
            )


def test_listed_distributions_are_in_the_existing_lockstep_check():
    locations, _ = versions.collect()
    projects = {
        tomllib.loads((d / "pyproject.toml").read_text())["project"]["name"]: d
        for d in versions.plugin_dirs()
    }
    for distribution in set(stability.STABLE_PLUGIN_COMMANDS.values()):
        assert distribution in projects
        rel = projects[distribution].relative_to(ROOT)
        assert any(loc.where == f"{rel}/pyproject.toml [project] version" for loc in locations)
        assert any(
            f"{rel}/pyproject.toml dependencies:" in loc.where and "sqllocks-shape==" in loc.where
            for loc in locations
        )


def test_exit_code_generator_includes_both_spellings_of_listed_commands(listed):
    text = generator.render()
    assert "`shape publish` / `shape fabric publish`" in text
    assert "| 0 | ok |" in text and "| 2 | bad input:" in text


def test_plugin_surface_is_checked_in_make_and_ci():
    for path in [ROOT / "Makefile", ROOT / ".github/workflows/ci.yml"]:
        assert "scripts/cli_surface.py --check --plugins" in path.read_text()
