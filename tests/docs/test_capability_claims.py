"""The docs guard uses registrations, including opposite-direction negative controls."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "check_capability_claims", ROOT / "scripts/check_capability_claims.py"
)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

pytestmark = pytest.mark.zero_network


@pytest.fixture(scope="module")
def items():
    return guard.capabilities()


@pytest.mark.parametrize(
    "sentence, fails",
    [
        ("Shape profiles Snowflake tables directly", True),
        ("Shape writes to Snowflake", False),
        ("Shape profiles Excel workbooks", False),
        ("Shape profiles XLSX workbooks", False),
        ("Shape profiles XLSB workbooks", True),
        ("Shape profiles UnregisteredFormat files", True),
        ("Shape profiles NDJSON files", False),
        ("Shape reads Excel files", False),
        ("Shape reads XLSB files", True),
        ("Shape reads Snowflake files", True),
        ("Shape profiles UnregisteredStore tables directly.", True),
        ("Shape reads UnregisteredStore.", True),
        ("Shape writes to UnregisteredStore.", True),
        ("Reads an NCPDP layout file you supply.", False),
        ("Shape reads SQL Server and Snowflake.", True),
        ("Shape reads Snowflake and SQL Server.", True),
        ("Shape profiles data from SQL Server, Snowflake and DuckDB.", True),
        ("Shape writes to X12.", False),
        ("Shape reads OneLake tables.", False),
        ("Shape writes to OneLake.", False),
        ("Reading from Snowflake is not supported.", False),
        ("COPY INTO reads it as https://account.blob.core.windows.net/container.", False),
        ("Shape reads Azure Blob Storage.", True),
        ("Shape reads S3 directly.", True),
        ("Shape writes to Google Cloud Storage.", True),
        ("Shape reads SQLite tables.", True),
        ("The plugin reads and writes dbt's files and never imports or runs dbt.", False),
        ("Reads the dbt project files you name.", False),
        ("Then reads the dbt job's run_results.json and manifest.json.", False),
        (
            "Needs a live workspace, not verified here: the job activity; "
            "how a notebook reads the model tables of a Fabric Data Warehouse (tableRoot)",
            False,
        ),
        ("How Shape reads Snowflake tables", True),
        ("## Does Shape read Snowflake and Databricks?", False),
        ("shape from-ddl FILE: reads SQL CREATE TABLE DDL", False),
        ("## Reading SQL DDL", False),
        ("Reading Snowflake tables is planned (W9-02)", False),
        ("Shape reads PostgreSQL through the integrations plugin", True),
        ("Shape does not read PostgreSQL tables directly in 0.9.1.", False),
        ("Shape cannot profile from Snowflake yet.", False),
        ("Shape profiles data from Snowflake.", True),
        ("Shape profiles data from\nSnowflake.", True),
        ("Shape reads tables from Snowflake.", True),
        ("Shape reads directly from Snowflake.", True),
        ("Shape reads live data in PostgreSQL.", True),
        ("Shape connects to Snowflake.", True),
        ("shape profile snowflake://account/db", True),
        ("shape profile kafka://broker/topic", True),
        ("shape stream-profile kafka://broker/topic", False),
        ("shape profile sqlserver://host/db", True),
        ("shape profile mssql://host/db", False),
        ("--to sql://scripts", True),
        ("shape profile postgresql://localhost/db", True),
        ("shape profile duckdb:///data.duckdb", False),
        ("--to snowflake://account/db", False),
        ("--to bigquery://project/dataset", True),
        ("Shape writes rows into BigQuery.", True),
        ("Shape writes to BigQuery in the next release (W9-13).", False),
        ("Reading Snowflake tables is planned (W9-01).", True),
        ("Shape reads Snowflake. Planned reading PostgreSQL (W9-01).", True),
        ("Export Snowflake tables to CSV, then profile the exported file.", False),
        ("Reading PostgreSQL and MySQL tables directly is planned (W9-01, issue #2).", False),
    ],
)
def test_controls(sentence, fails, items, tmp_path):
    page = tmp_path / "fake.md"
    page.write_text(sentence)
    assert bool(guard.check([page], items)) is fails


def test_registered_handlers_and_command_status(items):
    from gen_capabilities_page import GROUP_COMMAND, render

    from shape.cli.stability import level
    from shape.plugins.host import default_host

    host = default_host()
    for group in GROUP_COMMAND:
        for record in host.records(group):
            obj = host.get(group, record.name)
            if group == "shape.sources":
                assert callable(obj.read), f"{record.name} has no real source reader"
            direction = "reads" if group in {"shape.sources", "shape.stream_sources"} else "writes"
            assert any(
                c.name == record.name
                and c.package == record.source
                and c.direction == direction
                and set(obj.schemes).issubset(c.schemes)
                and c.command.startswith(GROUP_COMMAND[group].split()[0])
                for c in items
            )
    for c in items:
        assert c.status == (
            "experimental" if level(c.command.split()[0]) == "experimental" else "available"
        )
    page = render(items)
    assert "| Snowflake | no | yes |" in page
    assert "Status follows the stability of the command" in page


def test_public_scope(tmp_path):
    for name in (
        "README.md",
        "docs/page.md",
        "docs/plans/internal.md",
        "docs/talks/slides.md",
        "plugins/shape-fake/README.md",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("text")
    assert {p.relative_to(tmp_path).as_posix() for p in guard.source_files(tmp_path)} == {
        "README.md",
        "docs/page.md",
        "plugins/shape-fake/README.md",
    }


def test_every_profile_file_format_has_a_real_reader_and_table_entry(items, tmp_path):
    """Exercise the CLI's extension, workbook and delimiter-sniffing dispatch."""
    import subprocess

    import openpyxl
    import pyarrow as pa
    import pyarrow.parquet as pq

    from shape.io import readers
    from shape.io.excel import XLSX_SUFFIXES
    from shape.profile.reference import sources

    accepted = {}
    for suffix in {*sources._SUFFIXES, *readers._SUFFIX_KIND}:
        try:
            accepted[suffix] = sources._kind(Path("input" + suffix))
        except sources.SourceError:
            pass  # IO supports additional formats that the profile path dispatch does not accept.
    accepted.update(dict.fromkeys(XLSX_SUFFIXES, "xlsx"))
    for suffix, kind in accepted.items():
        assert any(
            c.direction == "reads" and c.command == "profile" and suffix in c.extensions
            for c in items
        ), f"profile accepts {suffix}, but the capability table has no read entry"
        path = tmp_path / ("input" + suffix)
        if kind == "csv":
            path.write_text("value,label\n1,first\n2,second\n")
        elif kind == "parquet":
            pq.write_table(pa.table({"value": [1, 2], "label": ["first", "second"]}), path)
        elif kind == "jsonl":
            path.write_text('{"value":1,"label":"first"}\n{"value":2,"label":"second"}\n')
        elif kind == "xlsx":
            book = openpyxl.Workbook()
            for row in (("value", "label"), (1, "first"), (2, "second")):
                book.active.append(row)
            book.save(path)
        else:
            pytest.fail(f"new profile format {kind}: add a real reader fixture")
        output = tmp_path / "profile.shape"
        result = subprocess.run(
            [sys.executable, "-m", "shape", "profile", str(path), "-o", str(output)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"{suffix}: {result.stderr}"
        assert output.is_file()
    for delimiter in readers._DELIMITERS:
        path = tmp_path / "sniff.csv"
        path.write_text(
            "\n".join(
                delimiter.join(row) for row in (("value", "label"), ("1", "first"), ("2", "second"))
            )
            + "\n"
        )
        assert readers.sniff_delimiter(path) == delimiter
        result = subprocess.run(
            [sys.executable, "-m", "shape", "profile", str(path), "-o", str(output)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
