"""The plugin loads, conforms to plugin API v1 (kit), and is in lockstep with core."""

import tomllib
from pathlib import Path

import pytest
import shape_databases
from shape_databases import DatabricksSink, MySqlSink, PostgresSink, SnowflakeSink
from shape_databases.testing import (
    FakeDatabricks,
    FakeServer,
    FakeSnowflake,
    sample_batch,
)

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


@pytest.mark.parametrize(
    ("sink_type", "fake_type", "uri"),
    [
        (SnowflakeSink, FakeSnowflake, "snowflake://shape@acct/DB/PUBLIC"),
        (DatabricksSink, FakeDatabricks, "databricks://h.example/sql/1.0/warehouses/w"),
    ],
)
def test_the_cloud_sinks_pass_the_kit_sink_check(sink_type, fake_type, uri):
    server = fake_type()
    names = sample_batch().schema.names
    kit.check_sink(
        sink_type(connect=server.connect),
        uri,
        [sample_batch(0, 4), sample_batch(4, 4)],
        read_back=lambda: __import__("pyarrow").table(
            {n: [r[i] for r in server.rows("kit_table")] for i, n in enumerate(names)}
        ),
    )
    assert len(server.rows("kit_table")) == 8


def test_names_and_schemes():
    assert (PostgresSink.name, PostgresSink.schemes) == ("postgres", ("postgresql", "postgres"))
    assert (MySqlSink.name, MySqlSink.schemes) == ("mysql", ("mysql",))
    assert (SnowflakeSink.name, SnowflakeSink.schemes) == ("snowflake", ("snowflake",))
    assert (DatabricksSink.name, DatabricksSink.schemes) == ("databricks", ("databricks",))


def test_the_installed_distribution_conforms():
    lines = kit.check_installed("sqllocks-shape-databases")
    assert [ln.split(":")[0] + ":" + ln.split(":")[1] for ln in lines] == [
        "shape.sinks:databricks",
        "shape.sinks:duckdb",
        "shape.sinks:mysql",
        "shape.sinks:postgres",
        "shape.sinks:snowflake",
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
    assert extras["duckdb"] == [f"{name}[duckdb]=={version}"]
    assert any(d.startswith("duckdb") for d in plugin["optional-dependencies"]["duckdb"])
    assert any(d.startswith("psycopg") for d in plugin["optional-dependencies"]["postgres"])
    assert any(d.lower().startswith("pymysql") for d in plugin["optional-dependencies"]["mysql"])
    # T-08: the two cloud drivers are extras of the plugin, and core has no extra for them
    assert [d.split(">")[0] for d in plugin["optional-dependencies"]["snowflake"]] == [
        "snowflake-connector-python"
    ]
    assert [d.split(">")[0] for d in plugin["optional-dependencies"]["databricks"]] == [
        "databricks-sql-connector"
    ]
    assert not {"snowflake", "databricks"} & set(extras)
    # T-07: the drivers are in no core dependency list
    for deps in [core["dependencies"], *extras.values()]:
        assert not any(
            d.lower().startswith(("psycopg", "pymysql", "snowflake", "databricks"))
            for d in deps
            if "sqllocks-shape-databases" not in d
        )
    # T-07: DuckDB is never a core dependency (extras such as dev and delta-fallback may name it)
    assert not any(d.lower().startswith("duckdb") for d in core["dependencies"])


def test_importing_the_plugin_does_not_import_a_driver():
    import subprocess
    import sys

    code = (
        "import sys, shape_databases; "
        "bad = [m for m in ('psycopg', 'pymysql', 'psycopg2', 'snowflake', 'databricks', "
        "'cryptography', 'duckdb') if m in sys.modules]; "
        "sys.exit(1 if bad else 0)"
    )
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0
