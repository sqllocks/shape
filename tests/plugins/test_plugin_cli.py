"""P2-03: ``shape plugins list|info`` and plugin-contributed commands, end to end.

The example plugin (``examples/plugin``) is installed as entry-point metadata on a temporary
path and the real ``shape`` CLI runs in a subprocess.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_SRC = ROOT / "examples" / "plugin" / "src"
ENTRY_POINTS = """\
[shape.commands]
hello = shape_example_plugin:HelloCommand
boom = broken_cmd:Boom
shadow = shape_example_plugin:HelloCommand
profile = shape_example_plugin:HelloCommand

[shape.detectors]
iban = shape_example_plugin:IbanDetector

[shape.sources]
lines = shape_example_plugin:LinesSource
"""
BROKEN = """
SHAPE_API = "1.0"

class Boom:
    name = "boom"
    help = "always fails"
    def configure(self, parser):
        pass
    def run(self, args):
        raise RuntimeError("kaboom")
"""


@pytest.fixture
def site(tmp_path):
    info = tmp_path / "shape_example_plugin-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: shape-example-plugin\nVersion: 0.1.0\n"
    )
    (info / "entry_points.txt").write_text(ENTRY_POINTS)
    (tmp_path / "broken_cmd.py").write_text(BROKEN)
    return tmp_path


def shape(site, *args):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(site), str(EXAMPLE_SRC)])
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from shape.cli.main import main; sys.exit(main())",
            *args,
        ],
        capture_output=True,
        text=True,
        env=env,
    )


def test_list_shows_every_plugin_without_importing_it(site):
    r = shape(site, "plugins", "list", "--json")
    assert r.returncode == 0, r.stderr
    rows = {(x["group"], x["name"]): x for x in json.loads(r.stdout)["payload"]}
    assert ("shape.sources", "lines") in rows and ("shape.detectors", "iban") in rows
    assert rows[("shape.commands", "hello")]["source"] == "shape-example-plugin"
    assert {x["status"] for x in rows.values()} == {"unloaded"}
    text = shape(site, "plugins", "list", "--group", "shape.detectors")
    assert "shape.detectors:iban" in text.stdout and "shape.sources" not in text.stdout


def test_info_loads_and_describes(site):
    r = shape(site, "plugins", "info", "lines")
    assert r.returncode == 0, r.stderr
    assert "shape.sources:lines" in r.stdout and "Source" in r.stdout and "lines" in r.stdout
    j = json.loads(shape(site, "plugins", "info", "shape.detectors:iban", "--json").stdout)
    assert j["status"] == "ok" and j["api"] == "1.0" and j["protocol"] == "SemanticDetector"
    assert "IBAN" in j["doc"]


def test_info_unknown_and_ambiguous_exit_2(site):
    r = shape(site, "plugins", "info", "nope")
    assert r.returncode == 2 and "no plugin 'nope'" in r.stderr
    (site / "shape_example_plugin-0.1.0.dist-info" / "entry_points.txt").write_text(
        "[shape.detectors]\ndup = shape_example_plugin:IbanDetector\n"
        "[shape.sources]\ndup = shape_example_plugin:LinesSource\n"
    )
    r = shape(site, "plugins", "info", "dup")
    assert (
        r.returncode == 2 and "shape.detectors:dup" in r.stderr and "shape.sources:dup" in r.stderr
    )
    assert shape(site, "plugins", "info", "shape.sources:dup").returncode == 0


def test_info_failed_plugin_exits_1(site):
    (site / "shape_example_plugin-0.1.0.dist-info" / "entry_points.txt").write_text(
        "[shape.detectors]\nbad = nonexistent_mod:Det\n"
    )
    r = shape(site, "plugins", "info", "bad")
    assert r.returncode == 1 and "error" in r.stdout and "nonexistent_mod" in r.stdout


def test_plugin_command_runs_with_its_own_arguments(site):
    r = shape(site, "hello", "--name", "shape")
    assert (r.returncode, r.stdout.strip()) == (0, "hello, shape")
    assert shape(site, "hello", "--bogus").returncode == 2


def test_plugin_command_failure_is_reported_not_raised(site):
    r = shape(site, "boom")
    assert r.returncode == 1 and "kaboom" in r.stderr and "Traceback" not in r.stderr


def test_builtin_names_win_and_help_lists_plugin_commands(site):
    r = shape(site, "profile", "--help")
    assert r.returncode == 0 and "SRC" in r.stdout  # still the built-in profile
    h = shape(site, "--help")
    assert "hello" in h.stdout and "(plugin shape-example-plugin)" in h.stdout
    assert "shadow" in h.stdout


def test_example_plugin_conforms_and_works(site):
    sys.path.insert(0, str(EXAMPLE_SRC))
    try:
        import pyarrow as pa
        import shape_example_plugin as ex

        from shape.plugins.api import v1

        assert isinstance(ex.LinesSource(), v1.Source)
        assert isinstance(ex.IbanDetector(), v1.SemanticDetector)
        assert isinstance(ex.HelloCommand(), v1.Command)
        det = ex.IbanDetector().detect(pa.array(["DE89370400440532013000"]), "acct")
        assert det is not None and det.label == "iban"
    finally:
        sys.path.remove(str(EXAMPLE_SRC))


def test_no_plugins_means_startup_unchanged(tmp_path):
    r = shape(tmp_path, "version")
    assert r.returncode == 0
