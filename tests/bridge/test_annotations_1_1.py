"""W7-04 item 9: schema annotations for clients: ``x-path``, ``x-name-or-path`` and ``effects``."""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import pytest

from shape.bridge.annotations import EFFECTS, NAME_OR_PATH, PATHS
from shape.bridge.registry import COMMANDS
from shape.bridge.schemas import all_schemas
from shape.bridge.spec import EFFECTS as KNOWN_EFFECTS

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "docs" / "bridge" / "schema"

#: What makes an argument look like a path: its description or its name ...
_DESCRIPTION = re.compile(
    r"\b(path|paths|file|files|folder|directory|dir|glob)\b|\.shape\b|\.json\b|\.yml\b|Delta table",
    re.I,
)
_NAME = re.compile(
    r"(^|_)(path|paths|dir|file|folder|output|input)$"
    r"|^(before|after|contract|config|schema|decisions|project|profile|source|data)$"
)
#: ... or how its handler uses it: handed to `Path(...)`, `open(...)`, a loader or a reader.
_HANDLER_USE = (
    r"(Path|open|load\w*|read\w*|stat)\(\s*(str\()?\w*\[?\s*args(\.get)?[\[(]\s*['\"]%s['\"]"
)

#: Arguments that look like a path to the checks above and are not one, and why.
NOT_A_PATH = {
    ("describe", "profile"): "a distribution profile name",
    ("dry_run", "profile"): "a distribution profile name",
    ("profile_info", "profile"): "a distribution profile name",
    ("generate", "profile"): "a distribution profile name",
    ("preview", "profile"): "a distribution profile name",
    ("scale_generate", "profile"): "a distribution profile name",
    ("stream", "profile"): "a distribution profile name",
    ("generate", "format"): "the name of a file format",
    ("profile", "dataset"): "how the source is read",
    ("profile", "version"): "a Delta table version",
    ("profile", "as_of"): "a Delta table time",
    ("diff", "source"): "the name of a source of the project file",
    ("check", "source"): "the name of a source of the project file",
    ("project_validate", "text"): "the file's text, not its path",
    ("contract_validate", "text"): "the contract's text, not its path",
    ("safe_scan", "text"): "the profile's text, not its path",
    ("design_from_data", "name"): "the design's name",
    ("bisect", "source"): "the name of a source of the project file",
    ("chaos", "format"): "the name of a file format",
    ("chaos", "allow_real_input"): "a flag",
    ("bisect", "column"): "a column name",
    ("rules_backtest", "since"): "a date",
    ("bisect_layers", "layers"): "names of sources of the project file",
}
#: Objects with paths inside them: no argument-level annotation can say which, so the command's
#: effects say what it does (writes files, reaches the network).
NESTED_PATHS = {("scale_generate", "sink_config"), ("stream", "sink_config")}


def all_args():
    return [(n, k, a) for n, c in COMMANDS.items() for k, a in c.args.items()]


def treats_as_path(name: str, arg_name: str, arg) -> bool:
    if _DESCRIPTION.search(arg.description) or _NAME.search(arg_name):
        return True
    handler = inspect.getsource(COMMANDS[name].handler)
    return bool(re.search(_HANDLER_USE % re.escape(arg_name), handler))


def test_every_argument_that_is_a_path_is_annotated():
    checked = 0
    for name, arg_name, arg in all_args():
        if (name, arg_name) in NOT_A_PATH or (name, arg_name) in NESTED_PATHS:
            assert arg.path is None, (name, arg_name)
            continue
        if treats_as_path(name, arg_name, arg):
            annotated = arg.path in ("read", "write") or arg.name_or_path
            assert annotated, f"{name}.{arg_name} is a path: annotate it"
            checked += 1
    assert checked >= 35  # the walk is not vacuous


def test_an_annotated_argument_is_one_that_looks_like_a_path():
    for name, arg_name, arg in all_args():
        if arg.path is not None:
            assert treats_as_path(name, arg_name, arg), (name, arg_name)
            assert arg.type in ("string", "array"), (name, arg_name)
            assert arg.path == "write" or "write" not in arg.description.lower().split("to ")[0]


def test_the_exceptions_are_real_arguments():
    for key in (*NOT_A_PATH, *NESTED_PATHS):
        assert key[1] in COMMANDS[key[0]].args, key


def test_the_1_0_arguments_are_annotated_in_the_published_1_1_schemas():
    for name, mapping in PATHS.items():
        schema = json.loads((SCHEMA_DIR / "commands" / f"{name}.request.schema.json").read_text())
        props = schema["properties"]["args"]["properties"]
        for arg, mode in mapping.items():
            assert props[arg]["x-path"] == mode, (name, arg)


def test_the_published_request_schemas_carry_x_path_for_every_path_argument():
    schemas = all_schemas()
    seen = 0
    for name, command in COMMANDS.items():
        props = schemas[f"commands/{name}.request.schema.json"]["properties"]["args"]["properties"]
        for arg_name, arg in command.args.items():
            assert props[arg_name].get("x-path") == arg.path, (name, arg_name)
            assert props[arg_name]["x-since"] == arg.since
            seen += arg.path is not None
    assert seen >= 25
    for name in COMMANDS:
        committed = json.loads(
            (SCHEMA_DIR / "commands" / f"{name}.request.schema.json").read_text()
        )
        assert committed == schemas[f"commands/{name}.request.schema.json"]


def test_write_paths_are_the_ones_a_command_writes():
    writes = {(n, k) for n, k, a in all_args() if a.path == "write"}
    assert writes == {
        ("generate", "output_dir"),
        ("profile", "output"),
        ("proposals_propose", "decisions"),
        ("proposals_decide", "decisions"),
        # bridge 1.2
        ("proposals_contract", "output"),
        ("report_card", "output"),
        ("chaos", "output_dir"),
        ("chaos", "ground_truth"),
        ("suite_run", "output_dir"),
    }


def test_domain_is_a_name_or_a_path_resolved_as_an_installed_name_first():
    domains = {n for n, k, a in all_args() if k == "domain" and a.name_or_path}
    assert (
        domains - {"chaos"}  # bridge 1.2's `chaos` takes a domain or a schema file too
        == set(NAME_OR_PATH)
        == {n for n, k, a in all_args() if k == "domain" and n in COMMANDS} - {"demo_run", "chaos"}
    )
    assert "chaos" in domains
    schemas = all_schemas()
    for name in domains:
        domain = schemas[f"commands/{name}.request.schema.json"]["properties"]["args"][
            "properties"
        ]["domain"]
        assert domain["x-name-or-path"] is True and "x-path" not in domain
    assert "x-name-or-path" not in json.dumps(
        schemas["commands/demo_run.request.schema.json"]
    )  # demo_run's domain is only a name
    from shape.generation.domains import domain_names

    assert "retail" in domain_names()  # a name is tried before a path: `retail` is not a file


def test_every_command_publishes_its_effects_in_the_index():
    index = all_schemas()["index.json"]["commands"]
    for name, command in COMMANDS.items():
        assert index[name]["effects"] == list(command.effects)
        assert set(command.effects) <= set(KNOWN_EFFECTS)
        assert len(set(command.effects)) == len(command.effects)
    assert KNOWN_EFFECTS == ("reads_files", "writes_files", "cancels", "network")
    committed = json.loads((SCHEMA_DIR / "index.json").read_text())["commands"]
    assert {n: c["effects"] for n, c in committed.items()} == {
        n: c["effects"] for n, c in index.items()
    }


def test_effects_agree_with_the_arguments():
    for name, command in COMMANDS.items():
        modes = {a.path for a in command.args.values() if a.path}
        if "write" in modes:
            assert "writes_files" in command.effects, name
        if "read" in modes or "write" in modes:
            assert "reads_files" in command.effects or "writes_files" in command.effects, name
        if command.cancellable or name.endswith("_cancel") or name == "stream_stop":
            assert "cancels" in command.effects, name
        if "sinks" in command.args or "token" in command.args or "scale_mode" in command.args:
            assert "network" in command.effects, name
        if (name, "sink_config") in NESTED_PATHS:
            assert "writes_files" in command.effects, name


def test_effects_that_only_read_never_claim_to_write():
    readers = {"describe", "dry_run", "validate", "diff", "check", "verify", "preview"}
    readers |= {"proposals_list", "project_validate", "project_show", "design", "design_from_data"}
    readers |= {"format_schema", "profile_show", "contract_validate", "safe_scan"}
    for name in readers:
        assert "writes_files" not in COMMANDS[name].effects, name
        assert "network" not in COMMANDS[name].effects, name
        assert "cancels" not in COMMANDS[name].effects, name
    assert COMMANDS["format_schema"].effects == ()
    assert COMMANDS["list"].effects == ()


def test_the_commands_that_write_say_so():
    writers = {n for n, c in COMMANDS.items() if "writes_files" in c.effects}
    assert writers == {
        "generate",
        "profile",
        "scale_generate",
        "stream",
        "proposals_propose",
        "proposals_decide",
        "demo_run",
        "demo_cleanup",
        # bridge 1.2: chaos, proposals_contract and report_card (with output)
        "proposals_contract",
        "report_card",
        "chaos",
        "suite_run",
    }
    assert {n for n, c in COMMANDS.items() if "cancels" in c.effects} == {
        "scale_generate",
        "stream",
        "stream_stop",
        "scale_cancel",
        "job_cancel",
        "rules_mutate",  # bridge 1.2: cancellable between mutants
        "chaos",  # bridge 1.2: cancellable before the files are written
        "suite_run",  # bridge 1.2: cancellable between scenarios
    }
    assert {n for n, c in COMMANDS.items() if "network" in c.effects} >= {
        "scale_generate",
        "stream",
    }


def test_the_effects_table_covers_every_1_0_command_and_names_no_other():
    old = {n for n, c in COMMANDS.items() if c.since == "1.0"}
    assert set(EFFECTS) == old
    new = {n for n, c in COMMANDS.items() if c.since == "1.1"}
    assert new and all(isinstance(COMMANDS[n].effects, tuple) for n in new)


@pytest.mark.parametrize("name", sorted(COMMANDS))
def test_the_request_schema_of_a_1_1_command_says_since_1_1(name):
    schema = json.loads((SCHEMA_DIR / "commands" / f"{name}.request.schema.json").read_text())
    assert schema["x-since"] == COMMANDS[name].since
    result = json.loads((SCHEMA_DIR / "commands" / f"{name}.result.schema.json").read_text())
    assert result["x-since"] == COMMANDS[name].since


def test_the_check_notices_a_path_argument_that_is_not_annotated():
    from shape.bridge.spec import Arg

    missing = Arg("string", "path of the thing to read")
    assert _DESCRIPTION.search(missing.description) and missing.path is None
    assert _NAME.search("output_dir") and _NAME.search("before") and not _NAME.search("seed")
    assert re.search(_HANDLER_USE % "x", 'Path(str(args["x"]))')
    assert re.search(_HANDLER_USE % "x", "open(args.get('x'))")
    assert not re.search(_HANDLER_USE % "x", 'int(args["x"])')
