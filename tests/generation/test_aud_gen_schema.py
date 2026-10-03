"""AUD-gen: the generation schema document (``GenSchema``)."""

from __future__ import annotations

from shape.generation.schema import GenSchema

_MINIMAL = {
    "schema_version": 1,
    "model": {"name": "m"},
    "tables": {
        "t": {
            "name": "t",
            "primary_key": ["id"],
            "columns": {
                "id": {"name": "id", "type": "integer", "generator": {"strategy": "sequence"}}
            },
        }
    },
}


def test_a_document_without_generation_reads_with_the_defaults():
    # 205: generation is optional in generation-schema-v1.json, but from_dict raised
    # KeyError: 'generation'.
    schema = GenSchema.from_dict(_MINIMAL)
    assert schema.generation.scale == "small"
    assert GenSchema.from_dict(schema.to_dict()).to_dict() == schema.to_dict()


def test_to_dict_writes_dataclass_rows_in_a_generator_as_json():
    # 205: inline AddressReference rows in a generator: TypeError: Object of type
    # AddressReference is not JSON serializable.
    from dataclasses import dataclass

    @dataclass
    class Row:
        city: str
        zip: str

    schema = GenSchema.from_dict(_MINIMAL)
    schema.tables["t"].columns["id"].generator["rows"] = [Row("Austin", "78701")]
    doc = schema.to_dict()
    assert doc["tables"]["t"]["columns"]["id"]["generator"]["rows"] == [
        {"city": "Austin", "zip": "78701"}
    ]
