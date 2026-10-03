"""W5-06 item 2: the Protobuf (proto3) importer, parsed without protoc."""

from __future__ import annotations

from pathlib import Path

import pytest
from import_fixtures import FIXTURES, columns, generate

from shape.importers import detect_format, import_schema
from shape.importers.core import ImportFormatError

SHOP = FIXTURES / "proto" / "shop.proto"


def test_messages_nested_messages_and_imports_become_tables() -> None:
    spec = import_schema(SHOP).spec.to_dict()
    assert set(spec["tables"]) == {
        "Order",
        "Order_Line",
        "Customer",
        "Sub",
        "Sub_Inner",
        "Money",
        "Order_tags",
        "Order_history",
        "Order_counters",
    }
    rels = {(r["child"], r["parent"], tuple(r["child_columns"])) for r in spec["relationships"]}
    assert ("Order", "Customer", ("customer_id",)) in rels
    assert ("Order_Line", "Order", ("Order_id",)) in rels  # repeated message
    assert ("Order_tags", "Order", ("Order_id",)) in rels  # repeated scalar
    assert ("Order_counters", "Order", ("Order_id",)) in rels  # map
    assert ("Order", "Money", ("price_id",)) in rels  # a message of the imported file


def test_field_types_enums_and_well_known_types() -> None:
    cols = columns(import_schema(SHOP).spec, "Order")
    assert cols["id"]["generator"] == {"strategy": "sequence", "start": 1}
    assert cols["total"]["type"] == "float" and cols["express"]["type"] == "boolean"
    assert cols["note"]["nullable"] is False and cols["coupon"]["nullable"] is True
    assert cols["status"]["generator"]["values"] == ["STATUS_UNKNOWN", "PLACED", "SHIPPED"]
    assert cols["placed_at"]["type"] == "timestamp"
    assert cols["label"]["type"] == "string" and cols["label"]["nullable"] is True
    assert cols["email"]["nullable"] is True and cols["phone"]["nullable"] is True
    assert "blob" not in cols and "eta" not in cols and "by_name" not in cols
    value = columns(import_schema(SHOP).spec, "Order_history")["value"]
    assert value["generator"]["values"] == ["STATUS_UNKNOWN", "PLACED", "SHIPPED"]
    counters = columns(import_schema(SHOP).spec, "Order_counters")
    assert set(counters) == {"key", "value", "Order_id", "id"}


def test_unrepresentable_elements_are_reported() -> None:
    skipped = {
        i["element"]: (i["kind"], i["reason"]) for i in import_schema(SHOP).report.not_imported
    }
    assert skipped["demo.shop.Order.blob"][0] == "field"
    assert "Duration is not imported" in skipped["demo.shop.Order.eta"][1]
    assert skipped["demo.shop.Order.by_name"][0] == "map"
    assert skipped["demo.shop.Order.email"][0] == "oneof"
    assert skipped["service Shop"][0] == "service"
    assert "import common.proto" not in skipped  # it was found next to the input


def test_the_imported_proto_spec_generates() -> None:
    tables = generate(import_schema(SHOP).spec)
    lines = set(tables["Order"].column("id").to_pylist())
    assert set(tables["Order_Line"].column("Order_id").to_pylist()) - {None} <= lines


def test_the_format_is_inferred_from_the_extension() -> None:
    assert detect_format(SHOP) == "protobuf"


def test_a_missing_import_leaves_its_types_as_string(tmp_path: Path) -> None:
    f = tmp_path / "a.proto"
    f.write_text(
        'syntax = "proto3";\nimport "gone.proto";\nmessage A { int32 id = 1; Gone g = 2; }\n'
    )
    result = import_schema(f)
    kinds = {i["kind"] for i in result.report.not_imported}
    assert {"import", "type"} <= kinds
    assert columns(result.spec, "A")["g"]["type"] == "string"


def test_an_import_cannot_leave_the_directory(tmp_path: Path) -> None:
    (tmp_path / "outer.proto").write_text('syntax = "proto3";\nmessage O { int32 id = 1; }\n')
    sub = tmp_path / "sub"
    sub.mkdir()
    f = sub / "a.proto"
    f.write_text(
        'syntax = "proto3";\nimport "../outer.proto";\nmessage A { int32 id = 1; O o = 2; }\n'
    )
    result = import_schema(f)
    assert any("not found in the directory" in i["reason"] for i in result.report.not_imported)
    assert "O" not in result.spec.to_dict()["tables"]


@pytest.mark.parametrize(
    ("text", "line", "needle"),
    [
        ('syntax = "proto2";\nmessage A {}\n', 1, "only proto3"),
        ("message A { int32 id = 1; }\n", 1, "starts with syntax"),
        (
            'syntax = "proto3";\nmessage A {\n  int32 id = 1;\n  string name = 1;\n}\n',
            4,
            "field number 1",
        ),
        ('syntax = "proto3";\nmessage A {\n  int32 id = 0;\n}\n', 3, "not allowed"),
        ('syntax = "proto3";\nmessage A {\n  int32 id = 1\n}\n', 4, "expected ';'"),
        ('syntax = "proto3";\nenum E {\n  X = 1;\n}\nmessage A { E e = 1; }\n', 3, "must be zero"),
        ('syntax = "proto3";\nmessage A {\n  Nope n = 1;\n}\n', 3, "'Nope' is not defined"),
        ('syntax = "proto3";\nmessage A {\n  required int32 n = 1;\n}\n', 3, "proto2"),
        ('syntax = "proto3";\nmessage A {\n  int32 n = 1;\n', 3, "end of file"),
        ('syntax = "proto3";\n/* open\nmessage A {}\n', 2, "unterminated comment"),
        ('syntax = "proto3";\nmessage A {\n  int32 n = 1; @\n}\n', 3, "unexpected character"),
    ],
)
def test_malformed_proto_names_the_file_and_line(
    tmp_path: Path, text: str, line: int, needle: str
) -> None:
    f = tmp_path / "bad.proto"
    f.write_text(text)
    with pytest.raises(ImportFormatError, match=needle) as info:
        import_schema(f)
    assert info.value.file == str(f) and info.value.line == line


def test_a_file_without_messages_is_refused(tmp_path: Path) -> None:
    f = tmp_path / "empty.proto"
    f.write_text('syntax = "proto3";\npackage x;\n')
    with pytest.raises(ImportFormatError, match="defines no message"):
        import_schema(f)
