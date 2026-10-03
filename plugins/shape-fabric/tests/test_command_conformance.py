"""Every command conforms to the plugin API (``shape.commands``): the conformance kit's rules
(a name of lowercase letters, digits and ``-``, a help text, ``configure`` accepting a parser, an
``int`` exit code) and the entry points that publish them."""

from __future__ import annotations

import importlib.metadata as md

import pytest
from shape_fabric import commands

from shape.plugins import kit

pytestmark = pytest.mark.contract

NAMES = {
    "fabric": commands.FabricCommand,
    "publish": commands.PublishCommand,
    "notebook": commands.NotebookCommand,
    "deploy-notebook": commands.DeployNotebookCommand,
    "setup-fabric": commands.SetupFabricCommand,
    "export-model": commands.ExportModelCommand,
    "known-answer": commands.KnownAnswerCommand,
    "check-answers": commands.CheckAnswersCommand,
    "publish-report": commands.PublishReportCommand,
}


def test_the_entry_points_publish_every_command():
    points = {
        ep.name: ep.value
        for ep in md.entry_points(group="shape.commands")
        if ep.value.startswith("shape_fabric.")
    }
    assert points == {name: f"shape_fabric.commands:{cls.__name__}" for name, cls in NAMES.items()}


@pytest.mark.parametrize("name", sorted(NAMES))
def test_names_and_help_follow_the_rules(name):
    cmd = NAMES[name]()
    assert cmd.name == name and cmd.help and cmd.help == cmd.help.strip()
    assert kit.check_common(cmd, "shape.commands") is None


def _argv(name, tmp):
    return {
        "fabric": ["setup", "--snippet"],
        "setup-fabric": ["--snippet"],
        "notebook": ["retail"],
        "export-model": ["retail", "-o", str(tmp / "m.bim")] if tmp else ["retail"],
        "known-answer": ["retail", "--scale", "fabric_demo", "-o", str(tmp / "ka")],
    }.get(name, [])


@pytest.mark.parametrize(
    "name", ["fabric", "setup-fabric", "notebook", "export-model", "known-answer"]
)
def test_the_kit_runs_each_command(name, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    kit.check_command(NAMES[name](), argv=_argv(name, tmp_path), expect_exit=0)


@pytest.mark.parametrize(
    "name", ["publish", "deploy-notebook", "known-answer", "check-answers", "publish-report"]
)
def test_a_command_missing_a_required_option_is_a_usage_error_not_a_traceback(name, tmp_path):
    from shape.cli.main import main

    assert main([name]) == 2


def test_no_command_imports_a_cloud_library_at_import_time():
    import subprocess
    import sys

    code = (
        "import sys, shape_fabric.commands; "
        "bad = [m for m in ('azure', 'pyodbc', 'adlfs', 'deltalake') if m in sys.modules]; "
        "print(bad)"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert done.stdout.strip() == "[]"
