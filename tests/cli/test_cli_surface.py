"""W1-10: the CLI surface snapshot (``scripts/cli_surface.py``) and the stability levels.

The baseline ``cli_surface_v1.json`` records the core parser as CLI 1.0 shipped it. These tests show
that the check passes on the live parser, that it fails and names what a stable command lost, that
additions pass, and that every command has a stability level (an experimental one says so in
``--help``).
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from shape.cli import stability
from shape.cli.main import _build_parser

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("cli_surface", ROOT / "scripts" / "cli_surface.py")
assert _spec is not None and _spec.loader is not None
surface = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(surface)

BASELINE = json.loads((ROOT / "tests" / "cli" / "cli_surface_v1.json").read_text("utf-8"))


def _subparser(parser: argparse.ArgumentParser, *path: str) -> argparse.ArgumentParser:
    for name in path:
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                parser = action.choices[name]
                break
    return parser


def _drop_flag(parser: argparse.ArgumentParser, *path_and_flag: str) -> None:
    *path, flag = path_and_flag
    target = _subparser(parser, *path)
    action = target._option_string_actions[flag]
    target._remove_action(action)
    for opt in action.option_strings:
        target._option_string_actions.pop(opt, None)


def _drop_command(parser: argparse.ArgumentParser, *path: str) -> None:
    parent = _subparser(parser, *path[:-1])
    for action in parent._actions:
        if isinstance(action, argparse._SubParsersAction):
            action.choices.pop(path[-1])
            action._choices_actions = [c for c in action._choices_actions if c.dest != path[-1]]


def _run_with(monkeypatch: pytest.MonkeyPatch, parser: argparse.ArgumentParser, *argv: str) -> int:
    monkeypatch.setattr(sys.modules["shape.cli.main"], "_build_parser", lambda *a, **k: parser)
    return int(surface.main(list(argv)))


# -- the baseline ------------------------------------------------------------------------------


def test_the_baseline_declares_its_format_and_an_integer_version() -> None:
    assert BASELINE["format"] == "shape-cli-surface"
    assert isinstance(BASELINE["version"], int) and BASELINE["version"] == 1


def test_a_baseline_from_a_newer_shape_or_another_format_is_refused(tmp_path: Path) -> None:
    newer = tmp_path / "newer.json"
    newer.write_text(json.dumps({**BASELINE, "version": 2}))
    with pytest.raises(ValueError, match="not readable"):
        surface.load_baseline(newer)
    other = tmp_path / "other.json"
    other.write_text(json.dumps({**BASELINE, "format": "something-else"}))
    with pytest.raises(ValueError, match="not a shape-cli-surface"):
        surface.load_baseline(other)


def test_the_baseline_is_what_write_produces() -> None:
    live = surface.snapshot()
    assert json.loads(json.dumps(live)) == BASELINE


def test_the_live_parser_is_compatible_with_the_baseline(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert surface.main(["--check"]) == 0
    assert "0 breaking change(s)" in capsys.readouterr().out


# -- stability levels --------------------------------------------------------------------------


def _commands(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:  # type: ignore[type-arg]
    return next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))


def test_every_stable_command_exists_in_the_parser() -> None:
    assert stability.STABLE <= set(_commands(_build_parser()).choices)


def test_the_core_workflow_commands_are_stable() -> None:
    core = {"capture", "profile", "diff", "check", "verify", "pack"}
    assert core <= stability.STABLE
    for name in core:
        assert BASELINE["commands"][name]["stability"] == "stable"


def test_every_experimental_command_says_so_in_its_help(capsys: pytest.CaptureFixture[str]) -> None:
    sub = _commands(_build_parser())
    experimental = [n for n in sub.choices if n not in stability.STABLE]
    assert experimental
    for name in experimental:
        with pytest.raises(SystemExit):
            _build_parser().parse_args([name, "--help"])
        assert "(experimental)" in capsys.readouterr().out, name
    listing = {c.dest: c.help or "" for c in sub._choices_actions}
    for name in experimental:
        if name in listing and listing[name]:
            assert listing[name].startswith("(experimental)"), name


def test_a_stable_command_does_not_say_experimental(capsys: pytest.CaptureFixture[str]) -> None:
    for name in sorted(stability.STABLE):
        with pytest.raises(SystemExit):
            _build_parser().parse_args([name, "--help"])
        assert "(experimental)" not in capsys.readouterr().out, name


def test_the_snapshot_records_the_level_of_every_command() -> None:
    for name, cmd in BASELINE["commands"].items():
        assert cmd["stability"] == stability.level(name)
        for sub in cmd["subcommands"].values():
            assert sub["stability"] == cmd["stability"]


# -- the check: removals fail and name what went, additions pass -------------------------------


def test_removing_a_stable_flag_exits_1_and_names_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    parser = _build_parser()
    _drop_flag(parser, "check", "--verify")
    assert _run_with(monkeypatch, parser, "--check") == 1
    out = capsys.readouterr().out
    assert "BREAKING: check --verify: flag was removed or renamed" in out


def test_renaming_a_stable_flag_is_a_removal_of_the_old_name(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    parser = _build_parser()
    check = _subparser(parser, "check")
    action = check._option_string_actions["--verify"]
    check._remove_action(action)
    check._option_string_actions.pop("--verify")
    check.add_argument("--verified", dest="verify")
    assert _run_with(monkeypatch, parser, "--check") == 1
    out = capsys.readouterr().out
    assert "check --verify" in out and "--verified" in out  # the old name breaks, the new is added


def test_adding_a_flag_exits_0_and_is_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    parser = _build_parser()
    _subparser(parser, "check").add_argument("--brand-new", action="store_true")
    assert _run_with(monkeypatch, parser, "--check") == 0
    assert "check --brand-new: new flag" in capsys.readouterr().out


def test_adding_a_command_and_a_subcommand_exits_0(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    parser = _build_parser()
    _commands(parser).add_parser("brand-new-command")
    pack = _subparser(parser, "pack")
    _commands(pack).add_parser("brand-new-sub")
    assert _run_with(monkeypatch, parser, "--check") == 0
    out = capsys.readouterr().out
    assert "brand-new-command: new experimental command" in out
    assert "pack brand-new-sub: new subcommand" in out


def test_adding_an_optional_positional_passes_and_a_required_one_breaks() -> None:
    live = copy.deepcopy(BASELINE)
    live["commands"]["check"]["positionals"].append({"name": "X", "nargs": "?", "choices": None})
    assert surface.compare(BASELINE, live)[0] == []
    live["commands"]["check"]["positionals"][-1]["nargs"] = None
    problems, _ = surface.compare(BASELINE, live)
    assert problems == ["check: new required positional 'X'"]


def test_removing_a_stable_command_or_subcommand_breaks(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    parser = _build_parser()
    _drop_command(parser, "diff")
    _drop_command(parser, "pack", "replay")
    assert _run_with(monkeypatch, parser, "--check") == 1
    out = capsys.readouterr().out
    assert "BREAKING: diff: command was removed or renamed" in out
    assert "BREAKING: pack replay: subcommand was removed or renamed" in out


def test_removing_a_positional_or_a_choice_breaks() -> None:
    live = copy.deepcopy(BASELINE)
    live["commands"]["check"]["positionals"].pop()
    problems, _ = surface.compare(BASELINE, live)
    assert problems == ["check: positional 'CONTRACT.json' was removed"]

    flag = next(
        (c, f, v)
        for c, cmd in BASELINE["commands"].items()
        if cmd["stability"] == "stable"
        for f, v in cmd["flags"].items()
        if v["choices"] and len(v["choices"]) > 1
    )
    live = copy.deepcopy(BASELINE)
    gone = flag[2]["choices"][0]
    live["commands"][flag[0]]["flags"][flag[1]]["choices"] = flag[2]["choices"][1:]
    problems, _ = surface.compare(BASELINE, live)
    assert problems == [f"{flag[0]} {flag[1]}: choice {gone!r} was removed"]


def test_adding_a_choice_passes_and_a_flag_that_starts_taking_no_value_breaks() -> None:
    cmd, flag, spec = next(
        (c, f, v)
        for c, cmd in BASELINE["commands"].items()
        if cmd["stability"] == "stable"
        for f, v in cmd["flags"].items()
        if v["choices"]
    )
    live = copy.deepcopy(BASELINE)
    live["commands"][cmd]["flags"][flag]["choices"] = [*spec["choices"], "zz-new"]
    assert surface.compare(BASELINE, live)[0] == []
    live = copy.deepcopy(BASELINE)
    live["commands"][cmd]["flags"][flag]["takes_value"] = not spec["takes_value"]
    assert len(surface.compare(BASELINE, live)[0]) == 1


def test_experimental_commands_may_change_and_stable_ones_may_not_be_demoted() -> None:
    live = copy.deepcopy(BASELINE)
    experimental = next(n for n, c in BASELINE["commands"].items() if c["stability"] != "stable")
    live["commands"].pop(experimental)
    assert surface.compare(BASELINE, live)[0] == []
    live["commands"]["check"]["stability"] = "experimental"
    assert surface.compare(BASELINE, live)[0] == ["check: demoted from stable to experimental"]


def test_a_removal_listed_as_deprecated_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    live = copy.deepcopy(BASELINE)
    live["commands"]["check"]["flags"].pop("--verify")
    assert surface.compare(BASELINE, live)[0] == ["check --verify: flag was removed or renamed"]
    monkeypatch.setattr(
        stability,
        "DEPRECATIONS",
        [{"path": "check --verify", "use": "--verified", "removed_in": "1.1"}],
    )
    problems, notes = surface.compare(BASELINE, live)
    assert problems == [] and notes == ["check --verify: removed after its deprecation"]


def test_a_global_flag_is_part_of_the_surface() -> None:
    live = copy.deepcopy(BASELINE)
    live["global_flags"].pop("--debug")
    assert surface.compare(BASELINE, live)[0] == ["shape --debug: flag was removed or renamed"]


# -- usage and write ---------------------------------------------------------------------------


def test_a_usage_error_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert surface.main([]) == 2
    assert surface.main(["--check", "--write"]) == 2
    assert surface.main(["--nonsense"]) == 2


def test_an_unreadable_baseline_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert surface.main(["--check", "--baseline", str(tmp_path / "missing.json")]) == 2
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert surface.main(["--check", "--baseline", str(bad)]) == 2
    assert "cli_surface: error" in capsys.readouterr().err


def test_write_refuses_a_breaking_change_and_accepts_an_addition(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "surface.json"
    target.write_text(json.dumps(BASELINE))
    broken = _build_parser()
    _drop_flag(broken, "check", "--verify")
    assert _run_with(monkeypatch, broken, "--write", "--baseline", str(target)) == 1
    assert json.loads(target.read_text()) == BASELINE
    grown = _build_parser()
    _subparser(grown, "check").add_argument("--brand-new", action="store_true")
    assert _run_with(monkeypatch, grown, "--write", "--baseline", str(target)) == 0
    assert "--brand-new" in json.loads(target.read_text())["commands"]["check"]["flags"]


def test_the_surface_check_runs_in_make_check() -> None:
    assert "scripts/cli_surface.py --check" in (ROOT / "Makefile").read_text("utf-8")
