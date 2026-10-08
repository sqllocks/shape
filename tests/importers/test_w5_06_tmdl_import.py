"""W5-06 item 2: the TMDL importer."""

from __future__ import annotations

from pathlib import Path

import pytest
from import_fixtures import FIXTURES, columns, generate

from shape.importers import detect_format, import_schema
from shape.importers.core import ImportFormatError
from shape.importers.tmdl import parse_tmdl, split_ref

MODEL = FIXTURES / "sales_model"


def _skipped() -> dict[str, tuple[str, str]]:
    result = import_schema(MODEL)
    return {i["element"]: (i["kind"], i["reason"]) for i in result.report.not_imported}


def test_the_folder_is_inferred_and_read_with_or_without_the_definition_folder() -> None:
    assert detect_format(MODEL) == "tmdl"
    assert detect_format(MODEL / "definition") == "tmdl"
    a = import_schema(MODEL).spec.dumps()
    b = import_schema(MODEL / "definition", "tmdl").spec.dumps()
    assert a == b
    single = import_schema(MODEL / "definition" / "tables" / "Calendar.tmdl")
    assert list(single.spec.to_dict()["tables"]) == ["Calendar"]
    assert detect_format(MODEL / "definition" / "tables" / "Calendar.tmdl") == "tmdl"


def test_tables_columns_and_types() -> None:
    spec = import_schema(MODEL).spec
    assert list(spec.to_dict()["tables"]) == [
        "Calendar",
        "Customer Master",
        "Passport",
        "Sales",
        "Tagging",
    ]
    sales = columns(spec, "Sales")
    assert sales["SaleId"]["type"] == "integer"
    assert sales["Amount"]["type"] == "decimal"
    assert sales["Cost"]["type"] == "float"
    assert sales["OrderDate"]["type"] == "date"  # dateTime with a date format
    assert sales["ShipTime"]["type"] == "timestamp"
    assert sales["Paid"]["type"] == "boolean"
    assert sales["Note"]["type"] == "string"
    assert sales["Weird"]["type"] == "string" and sales["Untyped"]["type"] == "string"
    assert "Receipt" not in sales and "Net Amount" not in sales
    # A quoted name with a doubled quote and a bracket keeps its text.
    assert "It's]odd" in columns(spec, "Customer Master")


def test_relationships_and_cardinality_become_foreign_keys_and_keys() -> None:
    spec = import_schema(MODEL).spec.to_dict()
    rels = {r["name"]: r for r in spec["relationships"]}
    r = rels["fk_Sales_CustomerKey"]
    assert (r["child"], r["parent"], r["type"]) == ("Sales", "Customer Master", "one_to_many")
    assert rels["fk_Passport_SaleId"]["type"] == "one_to_one"
    # fromCardinality one / toCardinality many swaps the sides: Tagging is the child.
    swapped = rels["fk_Tagging_TagDate"]
    assert (swapped["child"], swapped["parent"]) == ("Tagging", "Calendar")
    # The key of a table is the column its relationships point at, else its isKey column.
    assert spec["tables"]["Customer Master"]["primary_key"] == ["CustomerKey"]
    # A date key cannot be generated unique, so the calendar gets a generated key.
    assert spec["tables"]["Calendar"]["primary_key"] == ["id"]
    assert spec["tables"]["Tagging"]["columns"]["TagDate"]["type"] == "date"
    assert spec["tables"]["Sales"]["primary_key"] == ["SaleId"]
    # A table with no key gets a generated one.
    assert spec["tables"]["Tagging"]["primary_key"] == ["id"]
    fk = spec["tables"]["Sales"]["columns"]["CustomerKey"]
    assert fk["generator"]["ref"] == "Customer Master.CustomerKey"


def test_everything_that_is_not_structure_is_listed() -> None:
    skipped = _skipped()
    assert skipped["table Sales/measure Total Amount"][0] == "measure"
    assert skipped["table Sales/measure Margin"][0] == "measure"
    assert skipped["table Sales/column Net Amount"][0] == "calculated column"
    assert skipped["table Sales/partition Sales"][0] == "partition"
    assert skipped["table Sales/column Receipt"][0] == "column"
    assert "binary" in skipped["table Sales/column Receipt"][1]
    assert (
        "levels: Region, Customer Name" in skipped["table Customer Master/hierarchy Geography"][1]
    )
    assert "dataType 'variant' is not known" in skipped["table Sales/column Weird"][1]
    assert "no dataType" in skipped["table Sales/column Untyped"][1]
    assert skipped["role Reader"][0] == "role"
    assert "bothDirections" in skipped["relationship r_reversed"][1]


def test_an_inactive_relationship_is_a_key_and_says_so() -> None:
    mapped = {i["element"]: i["became"] for i in import_schema(MODEL).report.imported}
    assert mapped["relationship r_sales_order_date"].endswith("inactive in the model")


def test_the_imported_model_generates() -> None:
    tables = generate(import_schema(MODEL).spec)
    keys = set(tables["Customer Master"].column("CustomerKey").to_pylist())
    assert set(tables["Sales"].column("CustomerKey").to_pylist()) <= keys


def test_many_to_many_keeps_its_type(tmp_path: Path) -> None:
    f = tmp_path / "m.tmdl"
    f.write_text(
        "table A\n\tcolumn k\n\t\tdataType: int64\n"
        "table B\n\tcolumn k\n\t\tdataType: int64\n"
        "relationship r\n\tfromColumn: A.k\n\ttoColumn: B.k\n"
        "\tfromCardinality: many\n\ttoCardinality: many\n"
    )
    spec = import_schema(f).spec.to_dict()
    assert spec["relationships"][0]["type"] == "many_to_many"
    generate(import_schema(f).spec)


def test_the_parser_reads_expressions_and_quoted_names() -> None:
    nodes = parse_tmdl(
        "table 'A b'\n\tmeasure M = 1 +\n\t\t\t2\n\t\tformatString: 0\n\tcolumn c\n", "f.tmdl"
    )
    measure = nodes[0].children[0]
    assert nodes[0].name == "A b"
    assert measure.expr == "1 +\n2" and measure.prop("formatString") == "0"
    assert split_ref("'A b'.'c]d'", "f", 1, "r") == ("A b", "c]d")
    assert split_ref("A.B", "f", 1, "r") == ("A", "B")


@pytest.mark.parametrize(
    ("files", "needle", "line"),
    [
        (
            {
                "t.tmdl": "table A\n\tcolumn k\n\t\tdataType: int64\n"
                "relationship r\n\tfromColumn: A.k\n\ttoColumn: Nope.k\n"
            },
            "unknown table 'Nope'",
            4,
        ),
        (
            {
                "t.tmdl": "table A\n\tcolumn k\n\t\tdataType: int64\n"
                "relationship r\n\tfromColumn: A.zz\n\ttoColumn: A.k\n"
            },
            "unknown column 'zz'",
            4,
        ),
        (
            {
                "t.tmdl": "table A\n\tcolumn k\n\t\tdataType: int64\n"
                "relationship r\n\ttoColumn: A.k\n"
            },
            "no fromColumn",
            4,
        ),
        (
            {
                "t.tmdl": "table A\n\tcolumn k\n\t\tdataType: int64\n"
                "relationship r\n\tfromColumn: A.k\n\ttoColumn: A.k\n\tfromCardinality: lots\n"
            },
            "cardinality 'lots'",
            4,
        ),
        ({"t.tmdl": "table 'A\n\tcolumn k\n"}, "unterminated quote", 1),
        ({"t.tmdl": "table A\n  column k\n   dataType: int64\n"}, "not a multiple", 3),
        ({"t.tmdl": "table A\n \tcolumn k\n"}, "mixed", 2),
        ({"t.tmdl": "\tcolumn k\n"}, "unexpected indentation", 1),
        (
            {"t.tmdl": "table A\n\tcolumn k\n\t\tdataType: int64\ntable A\n\tcolumn k\n"},
            "defined twice",
            4,
        ),
        ({"t.tmdl": "table\n\tcolumn k\n"}, "needs a name", 1),
        ({"t.tmdl": "table A\n\tmeasure m = 1\n"}, "no importable column", 1),
        ({"t.tmdl": "model Model\n\tculture: en-US\n"}, "defines no table", None),
    ],
)
def test_malformed_tmdl_names_the_file_line_and_element(
    tmp_path: Path, files: dict[str, str], needle: str, line: int | None
) -> None:
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    with pytest.raises(ImportFormatError, match=needle) as info:
        import_schema(tmp_path, "tmdl")
    assert info.value.file in {str(tmp_path / n) for n in files} | {str(tmp_path)}
    if line is not None:
        assert info.value.line == line


def test_a_folder_without_tmdl_files_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ImportFormatError, match="no .tmdl files"):
        import_schema(tmp_path, "tmdl")
    (tmp_path / "x.txt").write_text("x")
    with pytest.raises(ImportFormatError, match="cannot tell the format|a folder"):
        import_schema(tmp_path)
