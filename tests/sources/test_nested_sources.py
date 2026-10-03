"""W5-07: nested JSON and XML sources (tables and struct modes, hostile inputs, round trip)."""

from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

import shape
from shape.builtins.sources import JsonlSource, JsonSource, XmlSource
from shape.io import ReaderError
from shape.io.nested import META_KIND, META_PATH, flatten_documents
from shape.plugins import kit
from shape.security.jsondepth import MAX_JSON_DEPTH, check_json_document
from shape.security.xmlsafe import UnsafeXml, parse_xml

ORDERS: list[dict[str, Any]] = [
    {
        "id": 1,
        "customer": {"name": "Ana", "address": {"city": "Oslo", "zip": "0150"}},
        "tags": ["new", "vip"],
        "lines": [{"sku": "A1", "qty": 2, "price": 9.5}, {"sku": "B2", "qty": 1, "price": 4.0}],
    },
    {
        "id": 2,
        "customer": {"name": "Bo", "address": {"city": "Rome", "zip": "00100"}},
        "tags": ["old"],
        "lines": [{"sku": "C3", "qty": 5, "price": 1.25}],
    },
]


def write(path: Path, doc: Any, *, lines: bool = False) -> str:
    if lines:
        path.write_text("".join(json.dumps(d) + "\n" for d in doc), encoding="utf-8")
    else:
        path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return str(path)


def nested(depth: int) -> str:
    return "[" * depth + "]" * depth


# -- 1. nested JSON ----------------------------------------------------------------------------


def test_tables_mode_splits_arrays_and_prefixes_objects(tmp_path):
    uri = write(tmp_path / "orders.json", ORDERS)
    got = JsonSource().read_tables(uri, flatten="tables")
    assert list(got) == ["orders", "orders__tags", "orders__lines"]
    root = got["orders"].to_pydict()
    assert root["_id"] == [1, 2]
    assert root["customer.name"] == ["Ana", "Bo"]
    assert root["customer.address.city"] == ["Oslo", "Rome"]
    assert "lines" not in root and "tags" not in root
    lines = got["orders__lines"].to_pydict()
    assert lines["_parent_id"] == [1, 1, 2]
    assert lines["_ordinal"] == [0, 1, 0]
    assert lines["sku"] == ["A1", "B2", "C3"]
    assert got["orders__lines"].schema.field("qty").type == pa.int64()
    assert got["orders__lines"].schema.field("price").type == pa.float64()
    tags = got["orders__tags"].to_pydict()
    assert tags["value"] == ["new", "vip", "old"]
    assert tags["_parent_id"] == [1, 1, 2]


def test_relationships_link_children_and_hold_in_the_data(tmp_path):
    from shape.generation.schema import Relationship

    src = JsonSource()
    uri = write(tmp_path / "orders.json", ORDERS)
    rels = src.read_relationships(uri, flatten="tables")
    tables = src.read_tables(uri, flatten="tables")
    assert all(isinstance(r, Relationship) for r in rels)
    assert {(r.parent, r.child) for r in rels} == {
        ("orders", "orders__tags"),
        ("orders", "orders__lines"),
    }
    for r in rels:
        parents = set(tables[r.parent].column(r.parent_columns[0]).to_pylist())
        children = set(tables[r.child].column(r.child_columns[0]).to_pylist())
        assert children <= parents
        assert r.type == "one_to_many"


def test_read_nested_returns_tables_relationships_and_warnings_in_one_parse(tmp_path):
    got = JsonSource().read_nested(write(tmp_path / "o.json", ORDERS), flatten="tables")
    assert set(got.tables) == {"o", "o__tags", "o__lines"}
    assert len(got.relationships) == 2 and got.warnings == []


def test_struct_mode_keeps_structs_and_lists(tmp_path):
    uri = write(tmp_path / "orders.json", ORDERS)
    schema = JsonSource().schema(uri, flatten="struct")
    assert pa.types.is_struct(schema.field("customer").type)
    assert pa.types.is_list(schema.field("tags").type)
    assert pa.types.is_list(schema.field("lines").type)
    batches = list(JsonSource().read(uri, flatten="struct"))
    assert sum(b.num_rows for b in batches) == 2


def test_struct_is_the_default_for_json_and_jsonl(tmp_path):
    uri = write(tmp_path / "orders.jsonl", ORDERS, lines=True)
    assert pa.types.is_struct(JsonlSource().schema(uri).field("customer").type)
    one = write(tmp_path / "o.json", ORDERS)
    assert pa.types.is_struct(JsonSource().schema(one).field("customer").type)


def test_jsonl_flatten_tables(tmp_path):
    uri = write(tmp_path / "orders.jsonl", ORDERS, lines=True)
    src = JsonlSource()
    got = src.read_tables(uri, flatten="tables")
    assert list(got) == ["orders", "orders__tags", "orders__lines"]
    assert got["orders__lines"].num_rows == 3
    assert src.read_relationships(uri, flatten="tables")[0].parent == "orders"
    child = pa.Table.from_batches(list(src.read(uri, flatten="tables", table="orders__lines")))
    assert child.num_rows == 3
    assert src.schema(uri, flatten="tables").names[:2] == ["_id", "id"]


def test_jsonl_without_flatten_is_unchanged(tmp_path):
    uri = write(tmp_path / "flat.jsonl", [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}], lines=True)
    table = pa.Table.from_batches(list(JsonlSource().read(uri)))
    assert table.to_pydict() == {"a": [1, 2], "b": ["x", "y"]}
    with pytest.raises(ValueError, match="flatten='tables'"):
        JsonlSource().schema(uri, table="x")


def test_json_single_document_and_single_record_array_agree(tmp_path):
    doc = ORDERS[0]
    a = JsonSource().read_tables(write(tmp_path / "a.json", doc), flatten="tables", name="x")
    b = JsonSource().read_tables(write(tmp_path / "b.json", [doc]), flatten="tables", name="x")
    assert {k: v.to_pydict() for k, v in a.items()} == {k: v.to_pydict() for k, v in b.items()}
    assert a["x"].num_rows == 1


def test_empty_array_and_empty_document(tmp_path):
    empty = JsonSource().read_tables(write(tmp_path / "e.json", []), flatten="tables")
    assert empty["e"].num_rows == 0 and empty["e"].column_names == ["_id"]
    doc = JsonSource().read_tables(write(tmp_path / "d.json", {}), flatten="tables")
    assert doc["d"].to_pydict() == {"_id": [1]}
    assert JsonSource().schema(write(tmp_path / "e2.json", []), flatten="struct").names == []


def test_arrays_inside_nested_objects_join_the_path_with_double_underscores(tmp_path):
    uri = write(tmp_path / "o.json", [{"customer": {"phones": [{"n": "1"}, {"n": "2"}]}}])
    got = JsonSource().read_tables(uri, flatten="tables")
    assert list(got) == ["o", "o__customer__phones"]
    assert got["o__customer__phones"].to_pydict()["n"] == ["1", "2"]


def test_empty_arrays_give_empty_child_tables(tmp_path):
    uri = write(tmp_path / "t.json", [{"id": 1, "items": [], "tags": []}])
    got = JsonSource().read_tables(uri, flatten="tables")
    assert got["t__items"].num_rows == 0 and got["t__tags"].num_rows == 0
    assert got["t"].to_pydict() == {"_id": [1], "id": [1]}


def test_mixed_type_arrays(tmp_path):
    uri = write(tmp_path / "m.json", [{"v": [1, "two", {"k": 3}, [4, 5]], "n": [1, 2.5]}])
    result = JsonSource().read_nested(uri, flatten="tables")
    v = result.tables["m__v"].to_pydict()
    assert v["_ordinal"] == [0, 1, 2, 3]
    assert v["value"] == ["1", "two", None, None]  # mixed scalar types read as string
    assert v["k"] == [None, None, 3, None]
    assert result.tables["m__v__value"].to_pydict()["value"] == [4, 5]
    assert any("m__v.value" in w and "mixed" in w for w in result.warnings)
    assert result.tables["m__n"].schema.field("value").type == pa.float64()


def test_mixed_types_cannot_be_a_struct_and_the_error_says_what_to_do(tmp_path):
    uri = write(tmp_path / "m.json", [{"v": [1, "x"]}])
    with pytest.raises(ReaderError, match="flatten='tables'"):
        JsonSource().schema(uri, flatten="struct")


def test_nulls_missing_keys_and_all_null_columns(tmp_path):
    uri = write(tmp_path / "n.json", [{"a": 1, "b": None}, {"a": None}, {"c": True}])
    t = JsonSource().read_tables(uri, flatten="tables")["n"]
    assert t.to_pydict() == {
        "_id": [1, 2, 3],
        "a": [1, None, None],
        "b": [None, None, None],
        "c": [None, None, True],
    }
    assert t.schema.field("b").type == pa.string()


def test_huge_integers_fall_back_to_string(tmp_path):
    uri = write(tmp_path / "h.json", [{"n": 2**70}, {"n": 1}])
    t = JsonSource().read_nested(uri, flatten="tables")
    assert t.tables["h"].column("n").to_pylist() == [str(2**70), "1"]
    assert t.warnings


def test_two_fields_flattening_to_one_table_name_are_refused():
    with pytest.raises(ReaderError, match="both flatten to the table name"):
        flatten_documents("r", [{"a__b": [1], "a": {"b": [2]}}])


def test_top_level_must_be_objects(tmp_path):
    for doc in (3, [1, 2], "text", [{"a": 1}, 5]):
        uri = write(tmp_path / "bad.json", doc)
        with pytest.raises(ReaderError, match="not an object"):
            JsonSource().read_tables(uri, flatten="tables")
        with pytest.raises(ReaderError, match="not an object"):
            JsonSource().schema(uri, flatten="struct")


def test_invalid_json_and_bad_encoding_are_reader_errors(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(ReaderError, match="cannot parse"):
        JsonSource().schema(str(p))
    p.write_bytes(b'{"a": "\xff\xfe"}')
    with pytest.raises(ReaderError, match="not UTF-8"):
        JsonSource().schema(str(p))
    q = tmp_path / "bad.jsonl"
    q.write_text('{"a": 1}\n{oops\n', encoding="utf-8")
    with pytest.raises(ReaderError, match="line 2"):
        JsonlSource().read_tables(str(q), flatten="tables")


def test_depth_limit_boundary(tmp_path):
    ok = tmp_path / "ok.json"
    ok.write_text(json.dumps({"a": json.loads(nested(MAX_JSON_DEPTH - 1))}))  # depth 128
    JsonSource().read_tables(str(ok), flatten="tables")
    deep = tmp_path / "deep.json"
    deep.write_text('{"a": ' + nested(MAX_JSON_DEPTH) + "}")  # depth 129
    with pytest.raises(ReaderError, match=f"deeper than {MAX_JSON_DEPTH} levels"):
        JsonSource().read_tables(str(deep), flatten="tables")
    with pytest.raises(ReaderError, match="128"):
        JsonSource().schema(str(deep), flatten="struct")


def test_depth_is_checked_across_lines_of_a_pretty_printed_document(tmp_path):
    levels = MAX_JSON_DEPTH + 5
    text = "".join('{"a":\n' for _ in range(levels)) + "1" + "}" * levels
    p = tmp_path / "pretty.json"
    p.write_text(text)
    with pytest.raises(ReaderError, match="deeper than"):
        JsonSource().schema(str(p))


def test_depth_limit_applies_to_jsonl_tables(tmp_path):
    p = tmp_path / "deep.jsonl"
    p.write_text('{"a": ' + nested(MAX_JSON_DEPTH + 1) + "}\n")
    with pytest.raises(ReaderError, match="deeper than"):
        JsonlSource().read_tables(str(p), flatten="tables")


def test_check_json_document_counts_across_chunks_and_resets_nothing():
    check_json_document(b"[" * 128 + b"]" * 128)
    with pytest.raises(ValueError, match="deeper than 128"):
        check_json_document(b"[" * 129 + b"]" * 129)
    with pytest.raises(ValueError, match="deeper than 3"):
        check_json_document(b"[[\n[[1]]]]", limit=3)
    check_json_document(b"[1]\n" * 1000, limit=1)  # siblings never add up
    check_json_document(b"")


def test_size_limit_names_the_limit(tmp_path):
    uri = write(tmp_path / "big.json", ORDERS)
    with pytest.raises(ReaderError, match=r"over the 10-byte limit"):
        JsonSource().schema(uri, max_bytes=10)
    JsonSource().schema(uri, max_bytes=10_000)


def test_options_are_validated(tmp_path):
    uri = write(tmp_path / "o.json", ORDERS)
    with pytest.raises(ValueError, match="flatten must be one of"):
        JsonSource().schema(uri, flatten="wide")
    with pytest.raises(TypeError, match="unexpected option"):
        JsonSource().schema(uri, record="//x")
    with pytest.raises(ValueError, match="no table 'nope'"):
        JsonSource().schema(uri, flatten="tables", table="nope")
    with pytest.raises(ValueError, match="not a json file"):
        JsonSource().schema(str(tmp_path / "o.txt"))


def test_can_open_only_json_files(tmp_path):
    src = JsonSource()
    assert src.can_open("data/a.json") and src.can_open("file:///x/A.JSON")
    assert not src.can_open("a.jsonl") and not src.can_open("abfss://c@a/x.json")
    assert not src.can_open("a.xml")


# -- 5. round trip -----------------------------------------------------------------------------


def rebuild(tables: dict[str, pa.Table], root: str) -> list[dict[str, Any]]:
    """Put a flattened document back together from its tables and their metadata."""
    children: dict[str, list[str]] = {}
    for name, t in tables.items():
        parent = (t.schema.metadata or {}).get(b"shape.nested.parent")
        if parent:
            children.setdefault(parent.decode(), []).append(name)

    def build_row(table: str, row: dict[str, Any]) -> Any:
        rec: dict[str, Any] = {}
        for col, val in row.items():
            if col in ("_id", "_parent_id", "_ordinal"):
                continue
            target = rec
            *prefix, last = col.split(".")
            for p in prefix:
                target = target.setdefault(p, {})
            target[last] = val
        for child in children.get(table, []):
            meta = tables[child].schema.metadata
            path = json.loads(meta[META_PATH])
            kind = meta[META_KIND].decode()
            items = [
                (r["_ordinal"], build_row(child, r))
                for r in tables[child].to_pylist()
                if r["_parent_id"] == row["_id"]
            ]
            items.sort(key=lambda x: x[0])
            values = [v["value"] if kind == "scalars" else v for _, v in items]
            target = rec
            for p in path[:-1]:
                target = target.setdefault(p, {})
            target[path[-1]] = values
        return rec

    return [build_row(root, r) for r in tables[root].to_pylist()]


def test_round_trip_rebuilds_the_original_document(tmp_path):
    doc = [
        *ORDERS,
        {
            "id": 3,
            "customer": {"name": "Cy", "address": {"city": "Kyiv", "zip": "01001"}},
            "tags": ["x"],
            "lines": [{"sku": "D4", "qty": 7, "price": 2.0, "notes": ["fragile", "gift"]}],
            "matrix": [[1, 2], [3]],
        },
    ]
    # fields a row lacks are null in the table, so give every row the same keys for equality
    doc[0].update(matrix=[[9]], **{})
    doc[1].update(matrix=[[8, 8]])
    for d in doc[:2]:
        for ln in d["lines"]:
            ln["notes"] = ["n"]
    tables = JsonSource().read_tables(write(tmp_path / "orders.json", doc), flatten="tables")
    assert rebuild(tables, "orders") == doc


def test_round_trip_through_jsonl(tmp_path):
    tables = JsonlSource().read_tables(
        write(tmp_path / "orders.jsonl", ORDERS, lines=True), flatten="tables"
    )
    assert rebuild(tables, "orders") == ORDERS


# -- 2. XML ------------------------------------------------------------------------------------

XML = """<?xml version="1.0"?>
<shop xmlns="urn:example:shop">
  <order id="1" status="open">
    <customer><name>Ana</name><city>Oslo</city></customer>
    <note>rush</note>
    <line sku="A1"><qty>2</qty></line>
    <line sku="B2"><qty>1</qty></line>
    <tag>new</tag><tag>vip</tag>
  </order>
  <order id="2" status="closed">
    <customer><name>Bo</name><city>Rome</city></customer>
    <line sku="C3"><qty>5</qty></line>
  </order>
  <meta>x</meta>
</shop>
"""


def write_xml(tmp_path: Path, text: str = XML, name: str = "shop.xml") -> str:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_xml_record_path_and_structure(tmp_path):
    got = XmlSource().read_nested(write_xml(tmp_path), record="//order")
    assert list(got.tables) == ["order", "order__line", "order__tag"]
    root = got.tables["order"].to_pydict()
    assert root["@id"] == ["1", "2"] and root["@status"] == ["open", "closed"]
    assert root["customer.name"] == ["Ana", "Bo"]
    assert root["note"] == ["rush", None]
    lines = got.tables["order__line"].to_pydict()
    assert lines["_parent_id"] == [1, 1, 2] and lines["_ordinal"] == [0, 1, 0]
    assert lines["@sku"] == ["A1", "B2", "C3"] and lines["qty"] == ["2", "1", "5"]
    assert got.tables["order__tag"].to_pydict()["value"] == ["new", "vip"]
    assert {(r.parent, r.child) for r in got.relationships} == {
        ("order", "order__line"),
        ("order", "order__tag"),
    }
    assert got.warnings == []


@pytest.mark.parametrize(
    "path", ["//order", "/shop/order", "order", "./order", ".//order", "//*/order", "/shop/*"]
)
def test_xml_record_path_forms(tmp_path, path):
    t = XmlSource().read_tables(write_xml(tmp_path), record=path)
    first = next(iter(t.values()))
    assert first.num_rows == (3 if path == "/shop/*" else 2)


def test_xml_record_path_below_the_document_and_the_root(tmp_path):
    uri = write_xml(tmp_path)
    assert XmlSource().read_tables(uri, record="//line")["line"].num_rows == 3
    assert XmlSource().read_tables(uri, record="//shop")["shop"].num_rows == 1
    assert XmlSource().read_tables(uri, record="/shop")["shop"].num_rows == 1


def test_xml_without_record_uses_the_most_repeated_element_and_warns(tmp_path):
    uri = write_xml(tmp_path)
    with pytest.warns(UserWarning, match="<order>"):
        got = XmlSource().read_nested(uri)
    assert "order" in got.tables and got.tables["order"].num_rows == 2
    assert "most repeated element <order> (2 under <shop>)" in got.warnings[0]


def test_xml_without_record_and_nothing_repeats_the_root_is_the_record(tmp_path):
    uri = write_xml(tmp_path, "<cfg><a>1</a><b>2</b></cfg>", "cfg.xml")
    with pytest.warns(UserWarning, match="root element is the one record"):
        got = XmlSource().read_tables(uri)
    assert got["cfg"].to_pydict() == {"_id": [1], "a": ["1"], "b": ["2"]}


def test_xml_empty_root_and_text_with_attributes(tmp_path):
    with pytest.warns(UserWarning):
        empty = XmlSource().read_tables(write_xml(tmp_path, "<empty/>", "e.xml"))
    assert empty["empty"].to_pydict() == {"_id": [1]}
    uri = write_xml(
        tmp_path, '<r><i><price cur="USD">3.5</price></i><i><price cur="EUR">4</price></i></r>'
    )
    got = XmlSource().read_tables(uri, record="//i")["i"].to_pydict()
    assert got["price.@cur"] == ["USD", "EUR"] and got["price.#text"] == ["3.5", "4"]


def test_xml_table_option_selects_a_child_table(tmp_path):
    uri = write_xml(tmp_path)
    child = pa.Table.from_batches(
        list(XmlSource().read(uri, record="//order", table="order__line"))
    )
    assert child.num_rows == 3
    assert XmlSource().schema(uri, record="//order").names[:2] == ["_id", "@id"]


def test_xml_record_path_errors(tmp_path):
    uri = write_xml(tmp_path)
    with pytest.raises(ReaderError, match="matches no element"):
        XmlSource().read_tables(uri, record="//nothing")
    for bad in ("//order[1]", "//order[@id='1']", "", "order/", "//", "a b"):
        with pytest.raises(ReaderError, match="unsupported record path"):
            XmlSource().read_tables(uri, record=bad)


def test_xml_billion_laughs_is_refused_before_parsing(tmp_path):
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
        '<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">]>'
        "<lolz>&lol3;</lolz>"
    )
    p = tmp_path / "bomb.xml"
    p.write_text(bomb)
    with pytest.raises(UnsafeXml, match="declares entities"):
        XmlSource().read_tables(str(p))


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le", "utf-16-be", "utf-32"])
def test_xml_entities_are_refused_in_other_encodings(tmp_path, encoding):
    text = '<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "y">]><a>&x;</a>'
    p = tmp_path / "enc.xml"
    p.write_bytes(text.encode(encoding))
    with pytest.raises(UnsafeXml):
        XmlSource().read_tables(str(p))


def test_xml_external_entity_is_refused_and_nothing_is_fetched(tmp_path, monkeypatch):
    import socket

    def no_network(*a: Any, **k: Any) -> None:
        raise AssertionError("the XML reader opened a connection")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    xxe = '<!DOCTYPE a [<!ENTITY x SYSTEM "http://127.0.0.1:9/secret">]><a>&x;</a>'
    p = tmp_path / "xxe.xml"
    p.write_text(xxe)
    with pytest.raises(UnsafeXml):
        XmlSource().read_tables(str(p))
    # a DOCTYPE that points at an external DTD and declares nothing is read, and never fetched
    q = tmp_path / "dtd.xml"
    q.write_text('<!DOCTYPE a SYSTEM "http://127.0.0.1:9/a.dtd"><a><b>1</b></a>')
    with pytest.warns(UserWarning):
        assert XmlSource().read_tables(str(q))["a"].to_pydict()["b"] == ["1"]


def test_parse_xml_accepts_text_and_bytes():
    assert parse_xml("<a/>").tag == "a" and parse_xml(b"<a/>").tag == "a"
    with pytest.raises(UnsafeXml):
        parse_xml("<!entity x 'y'><!ENTITY x 'y'><a/>")


def test_xml_depth_and_size_and_syntax_limits(tmp_path):
    deep = "<a>" * 129 + "</a>" * 129
    with pytest.raises(ReaderError, match="deeper than 128"):
        XmlSource().read_tables(write_xml(tmp_path, deep, "deep.xml"), record="//a")
    ok = "<a>" * 128 + "</a>" * 128
    XmlSource().read_tables(write_xml(tmp_path, ok, "ok.xml"), record="/a")
    with pytest.raises(ReaderError, match="cannot parse"):
        XmlSource().read_tables(write_xml(tmp_path, "<a><b></a>", "bad.xml"))
    with pytest.raises(ReaderError, match="over the 5-byte limit"):
        XmlSource().read_tables(write_xml(tmp_path), max_bytes=5)


def test_xml_options_and_can_open(tmp_path):
    uri = write_xml(tmp_path)
    with pytest.raises(TypeError, match="unexpected option"):
        XmlSource().schema(uri, flatten="tables")
    with pytest.raises(ValueError, match="no table"):
        XmlSource().schema(uri, record="//order", table="zzz")
    assert XmlSource().can_open("a.xml") and not XmlSource().can_open("a.json")
    assert not XmlSource().can_open("abfss://c@a/x.xml")


# -- conformance and profile ---------------------------------------------------------------------


def test_sources_pass_the_conformance_kit(tmp_path):
    kit.check_source(JsonSource(), write(tmp_path / "o.json", ORDERS))
    with pytest.warns(UserWarning, match="most repeated"):
        kit.check_source(XmlSource(), write_xml(tmp_path))
    kit.check_source(JsonlSource(), write(tmp_path / "o.jsonl", ORDERS, lines=True))


def test_sources_are_registered_as_builtins():
    from shape.plugins.host import default_host

    host = default_host()
    assert type(host.get("shape.sources", "json")).__name__ == "JsonSource"
    assert type(host.get("shape.sources", "xml")).__name__ == "XmlSource"


def test_profile_runs_on_every_table(tmp_path):
    tables = {
        **JsonSource().read_tables(write(tmp_path / "o.json", ORDERS), flatten="tables"),
        **XmlSource().read_tables(write_xml(tmp_path), record="//order"),
    }
    for name, table in tables.items():
        prof = shape.profile(table)
        (entry,) = prof.tables.values()
        assert entry["row_count"] == table.num_rows, name
        assert set(entry["columns"]) == set(table.column_names), name


def test_flatten_is_deterministic(tmp_path):
    uri = write(tmp_path / "o.json", ORDERS)
    a = JsonSource().read_tables(uri, flatten="tables")
    b = JsonSource().read_tables(uri, flatten="tables")
    assert all(a[k].equals(b[k]) for k in a)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        JsonSource().read_tables(uri, flatten="tables")
