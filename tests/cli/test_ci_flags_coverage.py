"""W1-14 deliverable 7: every core command has ``--json``, and ``--dry-run`` when it writes.

Walks the parsers of ``shape.cli.main`` (and the ``shape profile`` sub-commands). A command may lack
a flag only when ``ci_flags_exemptions.json`` says so, with a reason."""

from __future__ import annotations

import json
from pathlib import Path

from shape.cli.introspect import core_commands

EXEMPTIONS = json.loads((Path(__file__).parent / "ci_flags_exemptions.json").read_text())

#: Options that make a command write a file, a folder or a target.
WRITE_OPTIONS = {
    "--output", "--out", "--sink", "--to", "--html", "--junit", "--sarif",
}  # fmt: skip
#: Commands that write without such an option: they change a key, a registry, a project, a git
#: repository, the connection profiles, a job or the profile store.
STATE_WRITERS = {
    "keygen", "init", "git-setup", "proposals propose", "proposals decide", "profile validate",
    "demo cleanup", "demo init", "jobs cancel", "jobs resume", "registry commit",
    "registry tag", "registry promote", "profile registry save", "profile registry delete",
    "profile registry tag", "profile registry reindex",
    # it changes a pull request (W6-01): a `send` action, no file
    "ci post-comment",
    # it writes tables to a database (--target; W5-05), and has its own --dry-run
    "seed",
    # W6-03: they write a canary or game-day folder, and have their own --dry-run
    "canary make", "gameday run",
    # it rewrites registry logs and deletes objects (W8-03), and has its own --dry-run
    "registry prune",
}  # fmt: skip


def _flags(parser):
    return {o for a in parser._actions for o in a.option_strings}


def _writes(c) -> bool:
    return bool(_flags(c.parser) & WRITE_OPTIONS) or c.path in STATE_WRITERS


def test_exemptions_file_declares_its_format_and_a_reason_for_each_entry():
    assert EXEMPTIONS["format"] == "shape-ci-flags-exemptions" and EXEMPTIONS["version"] == 1
    for kind in ("json", "dry_run"):
        for path, reason in EXEMPTIONS[kind].items():
            assert isinstance(reason, str) and len(reason.split()) >= 4, (kind, path)


def test_every_core_command_has_json_unless_exempt():
    missing = [
        c.path
        for c in core_commands()
        if "--json" not in _flags(c.parser) and c.path not in EXEMPTIONS["json"]
    ]
    assert not missing, f"commands without --json that are not exempt: {missing}"


def test_every_writing_core_command_has_dry_run_unless_exempt():
    missing = [
        c.path
        for c in core_commands()
        if _writes(c)
        and "--dry-run" not in _flags(c.parser)
        and c.path not in EXEMPTIONS["dry_run"]
    ]
    assert not missing, f"commands that write without --dry-run and are not exempt: {missing}"


def test_exemptions_name_real_commands_that_really_lack_the_flag():
    by_path = {c.path: c for c in core_commands()}
    for path in EXEMPTIONS["json"]:
        assert path in by_path, f"stale exemption: {path}"
        assert "--json" not in _flags(by_path[path].parser), f"{path} has --json now"
    for path in EXEMPTIONS["dry_run"]:
        assert path in by_path, f"stale exemption: {path}"
        assert "--dry-run" not in _flags(by_path[path].parser), f"{path} has --dry-run now"


def test_a_command_that_does_not_write_has_no_dry_run():
    """--dry-run is for commands with something to plan: a read-only command does not get it."""
    for c in core_commands():
        if not _writes(c):
            assert "--dry-run" not in _flags(c.parser), c.path


def test_the_writing_commands_the_machine_layer_knows_are_the_ones_found_here():
    from shape.cli import machine

    found = {c.path for c in core_commands() if _writes(c)}
    native = machine.NATIVE_DRY_RUN
    assert found - native - set(EXEMPTIONS["dry_run"]) == set(machine.SPECS)


def test_the_walk_sees_the_commands_a_reader_would_expect():
    paths = {c.path for c in core_commands()}
    for expected in ("diff", "check", "verify", "fidelity", "generate", "emit", "registry commit",
                     "profile validate", "profile registry save", "bridge", "bridge schema",
                     "demo run", "jobs cancel", "pack run", "transform star"):  # fmt: skip
        assert expected in paths, expected
