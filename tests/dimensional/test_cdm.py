"""P6-06: the CDM folder output, and a test per fix of the baseline's defects."""

from __future__ import annotations

import datetime as dt
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from shape.dimensional import cdm_type, entity_name, model_document, write_cdm_folder


def test_entity_names():
    assert entity_name("order_line") == "OrderLine"
    assert entity_name("order line") == "OrderLine"
    assert entity_name("order", {"order": "SalesOrder"}) == "SalesOrder"
    assert entity_name("store", {"order": "SalesOrder"}) == "Store"


def test_types_follow_the_arrow_type_not_the_column_name():
    assert [cdm_type(t) for t in (pa.int32(), pa.float32(), pa.bool_(), pa.timestamp("us"))] == [
        "int64",
        "double",
        "boolean",
        "dateTime",
    ]
    assert cdm_type(pa.date32()) == "date"
    assert cdm_type(pa.decimal128(10, 2)) == "decimal"
    # a text column called *_date stays text: only the type says it is a date
    assert cdm_type(pa.string()) == "string"


def sample():
    return {
        "customer": pa.table({"customer_id": [1, 2], "joined_date": ["x", "y"]}),
        "order_line": pa.table({"line": [1.5], "at": [dt.datetime(2024, 1, 2, 3, 4, 5)]}),
    }


def test_model_document_shape():
    doc = model_document(sample(), "M", {"customer": "Contact"}, "csv", modified="T")
    assert doc["name"] == "M" and doc["modifiedTime"] == "T"
    assert [e["name"] for e in doc["entities"]] == ["Contact", "OrderLine"]
    contact = doc["entities"][0]
    assert contact["attributes"] == [
        {"name": "customer_id", "dataType": "int64"},
        {"name": "joined_date", "dataType": "string"},
    ]
    assert contact["partitions"][0]["location"] == "Contact/Contact.csv"
    assert contact["partitions"][0]["fileFormatSettings"]["$type"] == "CsvFormatSettings"
    parquet = model_document(sample(), "M", fmt="parquet")["entities"][0]["partitions"][0]
    assert parquet["fileFormatSettings"] == {"$type": "ParquetFormatSettings"}
    with pytest.raises(ValueError, match="unknown format"):
        model_document(sample(), "M", fmt="xml")


def test_folder_has_a_data_file_per_entity_and_the_manifest(tmp_path):
    files = write_cdm_folder(sample(), tmp_path / "cdm", "M", {"customer": "Contact"}, "parquet")
    assert [p.name for p in files] == ["Contact.parquet", "OrderLine.parquet", "model.json"]
    assert pq.read_table(tmp_path / "cdm/Contact/Contact.parquet").num_rows == 2
    model = json.loads((tmp_path / "cdm/model.json").read_text())
    assert model["entities"][1]["partitions"][0]["location"] == "OrderLine/OrderLine.parquet"


def test_csv_files(tmp_path):
    write_cdm_folder(sample(), tmp_path, "M")
    text = (tmp_path / "Customer/Customer.csv").read_text().splitlines()
    assert text[0] == '"customer_id","joined_date"'
    assert len(text) == 3


def test_fix_two_tables_with_one_entity_name_are_an_error(tmp_path):
    """The baseline wrote both to one folder, the second over the first, and listed both."""
    tables = {"a_b": pa.table({"x": [1]}), "A_b": pa.table({"x": [2]})}
    with pytest.raises(ValueError, match="both become the entity"):
        write_cdm_folder(tables, tmp_path, "M")
    assert not (tmp_path / "model.json").exists()
