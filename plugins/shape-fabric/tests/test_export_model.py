"""``shape fabric export-model DOMAIN`` (alias ``export-model``): the Power BI ``.bim`` export.

The model's content is pinned against the baseline by the 1:1 harness under ``benchmarks``; these
tests pin the command: every option, the alias, the shape of the document, and the quoting of names
that reach M and DAX.
"""

from __future__ import annotations

import json

import pytest
from shape_fabric.semantic_model import (
    SemanticModelExporter,
    dax_column,
    dax_table,
    m_identifier,
    m_text,
)

from shape.cli.main import main
from shape.generation.schema import GenSchema

pytestmark = pytest.mark.contract

BASELINE_NAME = "spin" + "dle"  # assembled: this file must not name the baseline either


@pytest.fixture
def run(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def go(*argv):
        code = main(list(argv))
        out = capsys.readouterr()
        return code, out.out, out.err

    return go


def bim(path):
    return json.loads(path.read_text(encoding="utf-8"))


def table(model, name):
    return next(t for t in model["model"]["tables"] if t["name"] == name)


def test_the_default_run_writes_model_bim_for_a_lakehouse(run, tmp_path):
    code, out, err = run("export-model", "retail")
    assert (code, err) == (0, "")
    model = bim(tmp_path / "model.bim")
    assert model["compatibilityLevel"] == 1604
    assert model["name"] == "ShapeRetail"
    names = [t["name"] for t in model["model"]["tables"]]
    assert {"customer", "order", "order_line", "product"} <= set(names)
    assert (
        "Lakehouse.Contents(null)"
        in table(model, "customer")["partitions"][0]["source"]["expression"]
    )
    assert "Source type:   lakehouse" in out and "Domain:        retail" in out
    n_tables = len(names)
    assert f"Tables:        {n_tables}" in out
    assert "model.bim" in out


def test_output_option_and_parent_folders(run, tmp_path):
    code, out, _ = run("export-model", "retail", "-o", str(tmp_path / "a" / "b" / "r.bim"))
    assert code == 0 and (tmp_path / "a" / "b" / "r.bim").is_file()
    code, _, _ = run("export-model", "retail", "--output", str(tmp_path / "long.bim"))
    assert code == 0 and (tmp_path / "long.bim").is_file()


@pytest.mark.parametrize("kind", ["warehouse", "sql_database"])
def test_source_type_and_source_name_make_the_sql_expression(run, tmp_path, kind):
    code, out, _ = run(
        "export-model", "retail", "--source-type", kind, "--source-name", "Sales", "-o", "m.bim"
    )
    assert code == 0 and f"Source type:   {kind}" in out
    expression = table(bim(tmp_path / "m.bim"), "customer")["partitions"][0]["source"]["expression"]
    assert 'Sql.Database("Sales", "Sales")' in expression
    assert 'Source{[Schema="dbo", Item="customer"]}[Data]' in expression


def test_schema_name_is_used_for_sql_sources(run, tmp_path):
    run(
        "export-model", "retail", "--source-type", "warehouse", "--schema-name", "sales",
        "-o", "m.bim",
    )  # fmt: skip
    expression = table(bim(tmp_path / "m.bim"), "customer")["partitions"][0]["source"]["expression"]
    assert 'Schema="sales"' in expression and "sales_customer" in expression


def test_measures_are_on_by_default_and_off_with_no_measures(run, tmp_path):
    run("export-model", "retail", "-o", "on.bim")
    run("export-model", "retail", "--include-measures", "-o", "on2.bim")
    run("export-model", "retail", "--no-measures", "-o", "off.bim")
    on, on2, off = (bim(tmp_path / f) for f in ("on.bim", "on2.bim", "off.bim"))
    assert on == on2
    counts = [sum(len(t.get("measures", [])) for t in m["model"]["tables"]) for m in (on, off)]
    assert counts[0] > 0 and counts[1] == 0
    customer = table(on, "customer")["measures"]
    assert customer[0] == {
        "name": "Customer Count",
        "expression": "COUNTROWS('customer')",
        "formatString": "#,0",
    }
    code, out, _ = run("export-model", "retail", "--no-measures", "-o", "off.bim")
    assert "DAX measures:  0" in out


def test_measures_total_and_average_decimals_and_total_integers(run, tmp_path):
    run("export-model", "retail", "-o", "m.bim")
    names = {m["name"] for m in table(bim(tmp_path / "m.bim"), "order_line")["measures"]}
    assert "Order Line Count" in names
    assert any(n.startswith("Total ") for n in names) and any(n.startswith("Avg ") for n in names)


def test_scale_option_is_accepted_and_changes_nothing_in_the_model(run, tmp_path):
    assert run("export-model", "retail", "-s", "medium", "-o", "a.bim")[0] == 0
    assert run("export-model", "retail", "--scale", "small", "-o", "b.bim")[0] == 0
    assert (tmp_path / "a.bim").read_bytes() == (tmp_path / "b.bim").read_bytes()


def test_relationships_keys_and_hidden_foreign_keys(run, tmp_path):
    run("export-model", "retail", "-o", "m.bim")
    model = bim(tmp_path / "m.bim")
    rel = next(r for r in model["model"]["relationships"] if r["fromTable"] == "order_line")
    assert rel["crossFilteringBehavior"] == "oneDirection" and rel["isActive"] is True
    order = table(model, "order")
    pk = next(c for c in order["columns"] if c.get("isKey"))
    assert pk["summarizeBy"] == "none"
    fk = next(c for c in order["columns"] if c["name"] == "customer_id")
    assert fk["isHidden"] is True


def test_the_alias_and_the_fabric_subcommand_write_the_same_file(run, tmp_path):
    assert run("export-model", "retail", "-o", "alias.bim")[0] == 0
    assert run("fabric", "export-model", "retail", "-o", "sub.bim")[0] == 0
    assert (tmp_path / "alias.bim").read_bytes() == (tmp_path / "sub.bim").read_bytes()


def test_the_output_is_deterministic(run, tmp_path):
    run("export-model", "retail", "-o", "1.bim")
    run("export-model", "retail", "-o", "2.bim")
    assert (tmp_path / "1.bim").read_bytes() == (tmp_path / "2.bim").read_bytes()


def test_mode_star_exports_the_star_schema(run, tmp_path):
    code, _, _ = run("export-model", "retail", "--mode", "star", "-o", "s.bim")
    assert code == 0
    model = bim(tmp_path / "s.bim")
    assert {a["name"]: a["value"] for a in model["model"]["annotations"]}["schema_mode"] == "star"


def test_a_generation_schema_file_can_be_exported(run, tmp_path):
    doc = _schema_doc("people")
    (tmp_path / "s.json").write_text(json.dumps(doc))
    code, out, _ = run("export-model", "s.json", "-o", "s.bim")
    assert code == 0 and [t["name"] for t in bim(tmp_path / "s.bim")["model"]["tables"]] == [
        "people"
    ]


def test_unknown_domain_is_exit_2_and_names_the_installed_ones(run, tmp_path):
    code, out, err = run("export-model", "nonesuch")
    assert code == 2 and "nonesuch" in err and "retail" in err
    assert not (tmp_path / "model.bim").exists()


def test_a_bad_source_type_is_exit_2(run):
    code, _, err = run("export-model", "retail", "--source-type", "kusto")
    assert code == 2 and "kusto" in err


def test_no_trace_of_the_baseline_in_the_output(run, tmp_path):
    code, out, err = run("export-model", "retail", "-o", "m.bim")
    text = out + err + (tmp_path / "m.bim").read_text()
    assert BASELINE_NAME not in text.lower()


def test_the_default_run_prints_a_hint_for_the_next_step(run):
    code, out, _ = run("export-model", "retail")
    assert "Tabular Editor" in out and "XMLA" in out


# --- quoting: names are data, not syntax --------------------------------------------------


def _schema_doc(table_name, column="name"):
    return {
        "schema_version": 1,
        "model": {"name": "t", "domain": "t", "seed": 1},
        "tables": {
            table_name: {
                "name": table_name,
                "primary_key": ["id"],
                "columns": {
                    "id": {
                        "name": "id",
                        "type": "integer",
                        "generator": {"strategy": "sequence"},
                    },
                    column: {
                        "name": column,
                        "type": "decimal",
                        "generator": {"strategy": "sequence"},
                    },
                },
            }
        },
        "relationships": [],
        "generation": {"scale": "small", "scales": {"small": {table_name: 3}}},
    }


def test_m_helpers_quote():
    assert m_text('a"b') == 'a""b'
    assert m_identifier("customer_Data") == "customer_Data"
    assert m_identifier('we"ird name') == '#"we""ird name"'
    assert dax_table("it's") == "'it''s'"
    assert dax_column("t", "a]b") == "'t'[a]]b]"


@pytest.mark.parametrize("kind", ["lakehouse", "warehouse", "sql_database"])
def test_a_hostile_table_name_cannot_break_out_of_the_m_expression(kind):
    schema = GenSchema.from_dict(_schema_doc('x"] & Web.Contents("evil") & ["'))
    model = SemanticModelExporter().to_dict(schema, source_type=kind, source_name='s"x')
    expression = model["model"]["tables"][0]["partitions"][0]["source"]["expression"]
    # every double quote of the name is doubled: the literal never ends inside the name
    assert 'x""] & Web.Contents(""evil"") & [""' in expression
    assert '"x"]' not in expression


def test_a_hostile_name_cannot_break_out_of_a_dax_reference():
    schema = GenSchema.from_dict(_schema_doc("o'rders", column="a]b"))
    model = SemanticModelExporter().to_dict(schema)
    expressions = [m["expression"] for m in model["model"]["tables"][0]["measures"]]
    assert "COUNTROWS('o''rders')" in expressions
    assert "SUM('o''rders'[a]]b])" in expressions


def test_an_unknown_source_type_is_refused_by_the_library():
    schema = GenSchema.from_dict(_schema_doc("t"))
    with pytest.raises(ValueError, match="unknown source type"):
        SemanticModelExporter().to_dict(schema, source_type="kusto")


def test_a_table_without_measures_has_no_measures_key():
    schema = GenSchema.from_dict(_schema_doc("t"))
    plain = SemanticModelExporter().to_dict(schema, include_measures=False)
    assert "measures" not in plain["model"]["tables"][0]
