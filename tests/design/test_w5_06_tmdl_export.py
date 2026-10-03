"""W5-06 items 4 and 5: the TMDL export of a star design and the round trip through the importer."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from shape.cli.main import main
from shape.design import DesignError, DesignInput
from shape.design.engine import derive
from shape.design.result import Column, ForeignKey, SchemaDesign, Table
from shape.design.tmdl import (
    dax_column,
    dax_table,
    m_identifier,
    m_text,
    tmdl_files,
    tmdl_name,
    write_tmdl,
)
from shape.generation.engine import Engine
from shape.generation.schema import GenSchema
from shape.importers import import_schema

ROOT = Path(__file__).resolve().parents[2]


def _star(doc: dict[str, Any], mode: str = "star") -> tuple[DesignInput, SchemaDesign]:
    design = DesignInput.from_dict(doc)
    return design, derive(design, mode)


def _read(folder: Path, name: str) -> str:
    return (folder / "definition" / name).read_text(encoding="utf-8")


def test_the_folder_has_the_files_of_a_power_bi_project(
    tmp_path: Path, doc: dict[str, Any]
) -> None:
    design, result = _star(doc)
    written = write_tmdl(result, tmp_path, source=design)
    rel = sorted(p.relative_to(tmp_path / "definition").as_posix() for p in written)
    assert rel == sorted(
        [
            "database.tmdl",
            "model.tmdl",
            "relationships.tmdl",
            *(f"tables/{t.name}.tmdl" for t in result.tables),
        ]
    )
    assert _read(tmp_path, "database.tmdl") == "database retail\n\tcompatibilityLevel: 1604\n"
    model = _read(tmp_path, "model.tmdl")
    assert "model Model\n\tculture: en-US" in model
    assert all(f"ref table {t.name}\n" in model for t in result.tables)


def test_tables_have_typed_columns_and_the_date_table_is_marked(
    tmp_path: Path, doc: dict[str, Any]
) -> None:
    design, result = _star(doc)
    write_tmdl(result, tmp_path, source=design)
    date = _read(tmp_path, "tables/dim_date.tmdl")
    assert date.startswith("table dim_date\n\tdataCategory: Time\n")
    assert (
        "\tcolumn date\n\t\tdataType: dateTime\n\t\tformatString: Short Date\n\t\tisKey\n" in date
    )
    fact = _read(tmp_path, "tables/fact_sales.tmdl")
    assert "\tcolumn amount\n\t\tdataType: decimal\n\t\tformatString: #,0.00\n" in fact
    assert "\tcolumn quantity\n\t\tdataType: int64\n" in fact
    assert "dataCategory" not in fact
    customer = _read(tmp_path, "tables/dim_customer.tmdl")
    assert "\tcolumn sk_customer\n\t\tdataType: int64\n\t\tisKey\n\t\tisHidden\n" in customer
    assert "\tcolumn name\n\t\tdataType: string\n" in customer
    assert "\tpartition dim_customer = m\n\t\tmode: import\n\t\tsource =\n\t\t\tlet\n" in customer


def test_relationships_come_from_the_surrogate_keys_and_role_playing_dates_are_inactive(
    tmp_path: Path, doc: dict[str, Any]
) -> None:
    design, result = _star(doc)
    write_tmdl(result, tmp_path, source=design)
    rels = _read(tmp_path, "relationships.tmdl")
    assert (
        "relationship fact_sales_sk_customer_dim_customer\n"
        "\tfromColumn: fact_sales.sk_customer\n\ttoColumn: dim_customer.sk_customer\n"
    ) in rels
    # Two dates, one date dimension: the first relationship is active, the second is not.
    assert "fact_sales.sk_order_date\n\ttoColumn: dim_date.sk_date\n\n" in rels
    assert "fact_sales.sk_ship_date\n\ttoColumn: dim_date.sk_date\n\tisActive: false\n" in rels
    assert rels.count("isActive: false") == 1
    assert rels.count("relationship ") == sum(len(t.foreign_keys) for t in result.tables)


def test_default_measures_follow_additivity(tmp_path: Path, doc: dict[str, Any]) -> None:
    doc["facts"][0]["measures"].append(
        {
            "name": "balance",
            "attribute": "quantity",
            "additivity": "semi_additive",
            "not_additive_over": ["order_date"],
        }
    )
    doc["facts"][0]["measures"].append({"name": "unrated", "attribute": "quantity"})
    design, result = _star(doc)
    write_tmdl(result, tmp_path, source=design)
    fact = _read(tmp_path, "tables/fact_sales.tmdl")
    assert "\tmeasure 'Sales Count' = COUNTROWS('fact_sales')\n\t\tformatString: #,0\n" in fact
    assert "\tmeasure 'Sales Total Quantity' = SUM('fact_sales'[quantity])\n" in fact
    assert (
        "\tmeasure 'Sales Total Amount' = SUM('fact_sales'[amount])\n\t\tformatString: #,0.00\n"
        in fact
    )
    # Non-additive, semi-additive and undeclared: no sum.
    for name in ("Discount Pct", "Balance", "Unrated"):
        assert f"Total {name}" not in fact
    assert fact.count("\tmeasure ") == 3
    assert "summarizeBy: sum" in fact.split("column quantity")[1].split("column ")[0]
    assert "summarizeBy: none" in fact.split("column discount_pct")[1].split("column ")[0]
    # Dimensions carry no measures.
    assert "measure" not in _read(tmp_path, "tables/dim_customer.tmdl")


def test_without_the_design_input_a_fact_gets_only_its_row_count(
    tmp_path: Path, doc: dict[str, Any]
) -> None:
    _, result = _star(doc)
    write_tmdl(result, tmp_path)
    fact = _read(tmp_path, "tables/fact_sales.tmdl")
    assert fact.count("\tmeasure ") == 1 and "COUNTROWS" in fact


def test_a_snowflake_design_is_exported_too(tmp_path: Path, doc: dict[str, Any]) -> None:
    design, result = _star(doc, "snowflake")
    write_tmdl(result, tmp_path, source=design)
    assert (tmp_path / "definition" / "tables" / "dim_customer_country.tmdl").exists()


def test_a_3nf_design_and_unusable_inputs_are_refused(tmp_path: Path, doc: dict[str, Any]) -> None:
    design, third = _star(doc, "3nf")
    with pytest.raises(DesignError, match="star or snowflake"):
        write_tmdl(third, tmp_path)
    assert not (tmp_path / "definition").exists()
    _, star = _star(doc)
    with pytest.raises(DesignError, match="unknown source type"):
        write_tmdl(star, tmp_path, source_type="nope")
    composite = SchemaDesign(
        "x",
        "star",
        (
            Table("a", "dimension", (Column("k", "integer"),), ("k",)),
            Table(
                "b",
                "fact",
                (Column("k1", "integer"), Column("k2", "integer")),
                (),
                (ForeignKey(("k1", "k2"), "a", ("k", "k")),),
            ),
        ),
    )
    with pytest.raises(DesignError, match="composite foreign key"):
        write_tmdl(composite, tmp_path)


def test_a_folder_holding_other_tmdl_files_is_refused_not_mixed(
    tmp_path: Path, doc: dict[str, Any]
) -> None:
    design, result = _star(doc)
    write_tmdl(result, tmp_path, source=design)
    stale = tmp_path / "definition" / "tables" / "old_table.tmdl"
    stale.write_text("table old_table\n")
    with pytest.raises(DesignError, match="old_table.tmdl"):
        write_tmdl(result, tmp_path, source=design)
    assert stale.exists()  # nothing was deleted
    stale.unlink()
    write_tmdl(result, tmp_path, source=design)  # an identical re-export is fine


def test_the_same_design_gives_byte_identical_files(tmp_path: Path, doc: dict[str, Any]) -> None:
    design, result = _star(doc)
    one, two = tmp_path / "one", tmp_path / "two"
    write_tmdl(result, one, source=design)
    write_tmdl(derive(DesignInput.from_dict(doc), "star"), two, source=design)
    files = sorted(p.relative_to(one) for p in (one / "definition").rglob("*.tmdl"))
    assert files == sorted(p.relative_to(two) for p in (two / "definition").rglob("*.tmdl"))
    for f in files:
        assert (one / f).read_bytes() == (two / f).read_bytes()
    assert tmdl_files(result, source=design) == tmdl_files(result, source=design)
    blob = b"".join((one / f).read_bytes() for f in files)
    assert b"\r" not in blob and not blob.startswith(b"\xef\xbb\xbf")


def test_output_does_not_depend_on_the_hash_seed(tmp_path: Path, doc: dict[str, Any]) -> None:
    src = tmp_path / "d.json"
    src.write_text(json.dumps(doc), encoding="utf-8")
    code = (
        "import sys\n"
        "from shape.design import load_design\n"
        "from shape.design.engine import derive\n"
        "from shape.design.tmdl import tmdl_files\n"
        "d = load_design(sys.argv[1])\n"
        "for mode in ('star', 'snowflake'):\n"
        "    for name, text in tmdl_files(derive(d, mode), source=d).items():\n"
        "        print('==', name); print(text)\n"
    )
    outputs = set()
    for seed in ("0", "1", "12345", "random"):
        done = subprocess.run(
            [sys.executable, "-c", code, str(src)],
            capture_output=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
            check=True,
            timeout=120,
        )
        outputs.add(done.stdout)
    assert len(outputs) == 1


# ---- names are quoted the way the Fabric plugin's semantic model quotes them ----------------


def _plugin() -> Any:
    path = ROOT / "plugins" / "shape-fabric" / "src" / "shape_fabric" / "semantic_model.py"
    if not path.exists():
        pytest.skip("the Fabric plugin source is not in this checkout")
    spec = importlib.util.spec_from_file_location("_shape_fabric_semantic_model", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("name", ["plain", "with space", "it's", "a]b", 'q"uote', "x'y]z \"w"])
def test_dax_and_m_quoting_match_the_plugin(name: str) -> None:
    plugin = _plugin()
    assert dax_table(name) == plugin.dax_table(name)
    assert dax_column(name, name) == plugin.dax_column(name, name)
    assert m_text(name) == plugin.m_text(name)
    assert m_identifier(name) == plugin.m_identifier(name)


def test_tmdl_names_are_quoted_when_they_are_not_plain_words() -> None:
    assert tmdl_name("fact_sales") == "fact_sales"
    assert tmdl_name("Dim Customer") == "'Dim Customer'"
    assert tmdl_name("it's") == "'it''s'"
    assert tmdl_name("1st") == "'1st'"


def test_a_table_with_awkward_characters_is_quoted_everywhere_and_read_back(
    tmp_path: Path, doc: dict[str, Any]
) -> None:
    doc["facts"][0]["name"] = "weird 'fact]"
    design, result = _star(doc)
    name = "fact_weird 'fact]"
    assert result.table(name).kind == "fact"
    write_tmdl(result, tmp_path, source=design)
    fact = next((tmp_path / "definition" / "tables").glob("fact_weird*.tmdl")).read_text()
    assert fact.startswith("table 'fact_weird ''fact]'\n")
    assert "COUNTROWS('fact_weird ''fact]')" in fact
    assert "SUM('fact_weird ''fact]'[amount])" in fact
    assert "partition 'fact_weird ''fact]' = m" in fact
    assert 'Data{[schema="fact_weird \'fact]"]}[Data]' in fact
    assert "ref table 'fact_weird ''fact]'" in (tmp_path / "definition" / "model.tmdl").read_text()
    imported = import_schema(tmp_path, "tmdl").spec.to_dict()
    assert name in imported["tables"]


# ---- the command ------------------------------------------------------------------------------


def _design_file(tmp_path: Path, doc: dict[str, Any]) -> Path:
    p = tmp_path / "retail.design.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def test_the_command_writes_tmdl_next_to_the_ddl(
    tmp_path: Path, doc: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    src = _design_file(tmp_path, doc)
    out = tmp_path / "model"
    code = main(
        ["design", str(src), "--mode", "star", "--tmdl", str(out), "-o", str(tmp_path / "r.sql")]
    )
    assert code == 0
    assert (out / "definition" / "model.tmdl").exists()
    assert "CREATE TABLE" in (tmp_path / "r.sql").read_text()
    assert "TMDL written to" in capsys.readouterr().err


def test_the_command_without_o_prints_no_ddl_when_it_writes_tmdl(
    tmp_path: Path, doc: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    src = _design_file(tmp_path, doc)
    assert main(["design", str(src), "--mode", "star", "--tmdl", str(tmp_path / "m")]) == 0
    assert "CREATE TABLE" not in capsys.readouterr().out


def test_the_command_refuses_tmdl_for_a_3nf_design(
    tmp_path: Path, doc: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    src = _design_file(tmp_path, doc)
    assert main(["design", str(src), "--mode", "3nf", "--tmdl", str(tmp_path / "m")]) == 2
    assert "star or snowflake" in capsys.readouterr().err
    assert not (tmp_path / "m").exists()


# ---- the round trip ---------------------------------------------------------------------------

#: A design type and the type the importer gives back for it. ``uuid`` and ``time`` are strings in
#: TMDL, so they come back as ``string``; ``date`` and ``timestamp`` are both ``dateTime`` and the
#: column's format tells them apart; ``binary`` columns are left out.
SURVIVING_TYPE = {
    "integer": "integer",
    "string": "string",
    "decimal": "decimal",
    "float": "float",
    "boolean": "boolean",
    "date": "date",
    "timestamp": "timestamp",
    "uuid": "string",
    "time": "string",
}


@pytest.mark.parametrize("mode", ["star", "snowflake"])
def test_round_trip_gives_back_tables_columns_types_and_relationships(
    tmp_path: Path, doc: dict[str, Any], mode: str
) -> None:
    """Survive: table names, column names and order, column types (with the mappings above), every
    relationship (child column and parent column, active or not) and the one-column keys of the
    dimensions. Do not survive: nullability, ``max_length``, precision and scale, the composite key
    of a fact or a bridge (the importer generates an ``id`` column for a table with no one-column
    key), the date dimension's key (it is keyed by its ``date`` column in TMDL; the importer keys it
    by the column its relationships point at), measures, and the generators."""
    design, result = _star(doc, mode)
    write_tmdl(result, tmp_path, source=design)
    imported = import_schema(tmp_path, "tmdl")
    spec = imported.spec.to_dict()
    assert set(spec["tables"]) == {t.name for t in result.tables}
    for t in result.tables:
        got = spec["tables"][t.name]["columns"]
        expected = [c.name for c in t.columns]
        extra = [c for c in got if c not in expected]
        assert [c for c in got if c in expected] == expected, t.name
        assert extra in ([], ["id"]), (
            t.name
        )  # the generated key of a table without a one-column key
        if extra:
            assert len(t.primary_key) != 1, t.name
        for c in t.columns:
            assert got[c.name]["type"] == SURVIVING_TYPE[c.type], (t.name, c.name)
        if len(t.primary_key) == 1 and t.kind != "date":
            assert spec["tables"][t.name]["primary_key"] == list(t.primary_key)
    designed = {
        (t.name, fk.columns[0], fk.ref_table, fk.ref_columns[0])
        for t in result.tables
        for fk in t.foreign_keys
    }
    got_rels = {
        (r["child"], r["child_columns"][0], r["parent"], r["parent_columns"][0])
        for r in spec["relationships"]
    }
    assert got_rels == designed
    # What comes back is a spec that generates.
    assert [p for p in imported.spec.validate() if p.level == "error"] == []
    for scale in spec["generation"]["scales"].values():
        for table in scale:
            scale[table] = 15
    tables = Engine(GenSchema.from_dict(spec), seed=5).generate().tables
    assert set(tables) == set(spec["tables"])
    assert all(t.num_rows > 0 for t in tables.values())
