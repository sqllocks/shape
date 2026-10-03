"""W5-06 item 2: the Avro importer."""

from __future__ import annotations

from pathlib import Path

import pytest
from import_fixtures import FIXTURES, columns, generate

from shape.importers import detect_format, import_schema
from shape.importers.core import ImportFormatError


def test_records_nested_records_arrays_and_maps_become_tables() -> None:
    spec = import_schema(FIXTURES / "trip.avsc").spec.to_dict()
    assert set(spec["tables"]) == {"Trip", "Rider", "Leg", "Trip_stops", "Trip_extras"}
    rels = {(r["child"], r["parent"], tuple(r["child_columns"])) for r in spec["relationships"]}
    assert ("Rider", "Trip", ("Trip_id",)) in rels  # an inline record is a child table
    # A named record is a reference, unless it closes a cycle (Rider is a child of Trip):
    # the reference is the one broken, and the report says so.
    assert ("Trip", "Rider", ("backup_rider_id",)) not in rels
    assert ("Leg", "Trip", ("Trip_id",)) in rels
    assert ("Trip_stops", "Trip", ("Trip_id",)) in rels
    assert ("Trip_extras", "Trip", ("Trip_id",)) in rels
    assert set(columns(import_schema(FIXTURES / "trip.avsc").spec, "Trip_extras")) == {
        "key",
        "value",
        "Trip_id",
        "id",
    }


def test_a_cycle_of_foreign_keys_is_broken_at_the_reference_and_reported() -> None:
    result = import_schema(FIXTURES / "trip.avsc")
    cycle = [i for i in result.report.not_imported if i["kind"] == "foreign key"]
    assert len(cycle) == 1
    assert (
        "Trip.backup_rider_id -> Rider closes a cycle (Trip -> Rider -> Trip)" in cycle[0]["reason"]
    )
    col = columns(result.spec, "Trip")["backup_rider_id"]
    assert col["generator"]["strategy"] == "distribution" and col["nullable"] is True


def test_logical_types_enums_unions_and_decimals() -> None:
    cols = columns(import_schema(FIXTURES / "trip.avsc").spec, "Trip")
    assert cols["trip_id"]["generator"] == {"strategy": "sequence", "start": 1}
    assert cols["day"]["type"] == "date"
    assert cols["started"]["type"] == "timestamp" and cols["ended"]["type"] == "timestamp"
    assert cols["ended"]["nullable"] is True and cols["started"]["nullable"] is False
    assert cols["ticket"]["type"] == "uuid"
    assert cols["mode"]["generator"]["values"] == ["bus", "tram", "ferry"]
    assert cols["return_mode"]["generator"]["values"] == ["bus", "tram", "ferry"]  # by name
    fare = cols["fare"]
    assert (fare["type"], fare["precision"], fare["scale"]) == ("decimal", 6, 2)
    assert (fare["generator"]["min"], fare["generator"]["max"]) == (0, 9999)
    assert cols["distance"]["type"] == "float" and cols["express"]["type"] == "boolean"


def test_unrepresentable_elements_are_reported() -> None:
    result = import_schema(FIXTURES / "trip.avsc")
    skipped = {i["element"]: (i["kind"], i["reason"]) for i in result.report.not_imported}
    spec = result.spec.to_dict()["tables"]["Trip"]["columns"]
    assert "photo" not in spec and "badge" not in spec
    assert skipped["#/fields/15/type"][0] == "bytes"
    assert any(k == "fixed" for k, _ in skipped.values())
    assert skipped["#/fields/17/type"][1] == "union with 2 non-null branches: imported as string"
    assert skipped["#/fields/18/type"][1].startswith("array of arrays")
    assert any("duration-ish" in r for _, r in skipped.values())
    assert spec["payload"]["type"] == "string"


def test_the_imported_avro_spec_generates() -> None:
    tables = generate(import_schema(FIXTURES / "trip.avsc").spec)
    riders = set(tables["Rider"].column("rider_id").to_pylist())
    assert set(tables["Rider"].column("Trip_id").to_pylist()) <= set(
        tables["Trip"].column("trip_id").to_pylist()
    )
    assert riders
    fares = [f for f in tables["Trip"].column("fare").to_pylist() if f is not None]
    assert fares and max(float(f) for f in fares) <= 9999


def test_the_format_is_inferred_from_the_extension_and_the_content(tmp_path: Path) -> None:
    assert detect_format(FIXTURES / "trip.avsc") == "avro"
    as_json = tmp_path / "trip.json"
    as_json.write_text((FIXTURES / "trip.avsc").read_text())
    assert detect_format(as_json) == "avro"


@pytest.mark.parametrize(
    ("text", "needle", "element"),
    [
        ('{"type":"record","name":"A"}', "non-empty fields", "#"),
        ('{"type":"record","fields":[{"name":"x","type":"int"}]}', "needs a name", "#"),
        ('{"type":"record","name":"A","fields":[{"name":"x"}]}', "has no type", "#/fields/0"),
        (
            '{"type":"record","name":"A","fields":[{"name":"x","type":"nope"}]}',
            "unknown type 'nope'",
            "#/fields/0/type",
        ),
        (
            '{"type":"record","name":"A","fields":[{"name":"x","type":{"type":"enum","name":"E","symbols":[]}}]}',
            "symbols",
            "#/fields/0/type",
        ),
        (
            '{"type":"record","name":"A","fields":[{"name":"x","type":{"type":"array"}}]}',
            "needs items",
            "#/fields/0/type",
        ),
        (
            '{"type":"record","name":"A","fields":[{"name":"x","type":{"type":"bytes","logicalType":"decimal","precision":2,"scale":5}}]}',
            "scale",
            "#/fields/0/type",
        ),
        ('"string"', "must be a record", "#"),
    ],
)
def test_malformed_avro_names_the_element(
    tmp_path: Path, text: str, needle: str, element: str
) -> None:
    f = tmp_path / "bad.avsc"
    f.write_text(text)
    with pytest.raises(ImportFormatError, match=needle) as info:
        import_schema(f)
    assert info.value.file == str(f) and info.value.element == element


def test_a_syntax_error_has_the_line(tmp_path: Path) -> None:
    f = tmp_path / "bad.avsc"
    f.write_text('{\n "type": "record",\n "name": }\n')
    with pytest.raises(ImportFormatError) as info:
        import_schema(f)
    assert info.value.line == 3 and "not valid JSON" in str(info.value)
