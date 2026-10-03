"""The plugin loads, conforms to plugin API v1 (kit), and is in lockstep with core."""

import tomllib
from pathlib import Path

import pytest
import shape_databases
from shape_databases import MySqlSink, PostgresSink
from shape_databases.testing import FakeServer, sample_batch

from shape.plugins import kit

ROOT = Path(__file__).resolve().parents[3]


def test_declares_the_supported_plugin_api():
    assert kit.check_module_api(shape_databases) == "1.0"


@pytest.mark.parametrize(
    ("sink_type", "dialect", "uri"),
    [
        (PostgresSink, "postgres", "postgresql://shape@h/db"),
        (MySqlSink, "mysql", "mysql://shape@h/db"),
    ],
)
def test_each_sink_passes_the_kit_sink_check(sink_type, dialect, uri):
    server = FakeServer(dialect)
    sink = sink_type(connect=server.connect)
    kit.check_sink(
        sink,
        uri,
        [sample_batch(0, 4), sample_batch(4, 4)],
        read_back=lambda: __import__("pyarrow").table(
            {
                n: [r[i] for r in server.rows("kit_table")]
                for i, n in enumerate(sample_batch().schema.names)
            }
        ),
    )
    assert len(server.rows("kit_table")) == 8


def test_names_and_schemes():
    assert (PostgresSink.name, PostgresSink.schemes) == ("postgres", ("postgresql", "postgres"))
    assert (MySqlSink.name, MySqlSink.schemes) == ("mysql", ("mysql",))


def test_the_installed_distribution_conforms():
    lines = kit.check_installed("sqllocks-shape-databases")
    assert [ln.split(":")[0] + ":" + ln.split(":")[1] for ln in lines] == [
        "shape.sinks:mysql",
        "shape.sinks:postgres",
    ]


def test_lockstep_and_extras_with_core():
    core = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    plugin = tomllib.loads(
        (ROOT / "plugins" / "shape-databases" / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]
    version = core["version"]
    assert plugin["version"] == version
    assert plugin["dependencies"] == [f"sqllocks-shape=={version}"]  # no heavy client in the base
    extras = core["optional-dependencies"]
    name = plugin["name"]
    assert extras["postgres"] == [f"{name}[postgres]=={version}"]
    assert extras["mysql"] == [f"{name}[mysql]=={version}"]
    assert extras["databases"] == [f"{name}[postgres,mysql]=={version}"]
    assert any(d.startswith("psycopg") for d in plugin["optional-dependencies"]["postgres"])
    assert any(d.lower().startswith("pymysql") for d in plugin["optional-dependencies"]["mysql"])
    # T-07: the drivers are in no core dependency list
    for deps in [core["dependencies"], *extras.values()]:
        assert not any(d.lower().startswith(("psycopg", "pymysql")) for d in deps)


def test_importing_the_plugin_does_not_import_a_driver():
    import subprocess
    import sys

    code = (
        "import sys, shape_databases; "
        "bad = [m for m in ('psycopg', 'pymysql', 'psycopg2') if m in sys.modules]; "
        "sys.exit(1 if bad else 0)"
    )
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0
