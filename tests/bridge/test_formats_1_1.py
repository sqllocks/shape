"""W7-04 item 6: ``format_schema``."""

from __future__ import annotations

import json
from importlib import resources

import pytest

from shape.bridge.handlers.formats import FORMATS, formats_at, named_files, shipped_files


def test_every_shipped_schema_file_is_in_the_table_and_the_table_names_only_shipped_files():
    shipped = set(shipped_files())
    assert shipped == named_files()
    assert len({f.file for f in FORMATS.values()}) == len(FORMATS)  # one name per file
    assert len(shipped) >= 8  # a test that walks nothing would pass vacuously


def test_a_new_schema_file_without_a_name_is_reported(monkeypatch):
    import shape.bridge.handlers.formats as formats

    real = formats.shipped_files()
    monkeypatch.setattr(formats, "shipped_files", lambda: [*real, "new-v1.json"])
    assert set(formats.shipped_files()) - named_files() == {"new-v1.json"}


def test_without_a_name_the_result_lists_the_formats(api11, api12):
    result = api11.ok("format_schema")
    assert result == {"names": sorted(formats_at(1))}  # what 1.1 listed: nothing 1.2 added
    assert {"design-input", "generation-schema", "decisions", "project"} <= set(result["names"])
    assert api12.ok("format_schema") == {"names": sorted(FORMATS)}


@pytest.mark.parametrize("name", sorted(FORMATS))
def test_each_format_gives_its_schema_format_and_version(api12, name):
    result = api12.ok("format_schema", name=name)
    assert result["name"] == name and set(result) == {"name", "format", "version", "schema"}
    known = FORMATS[name]
    text = resources.files("shape").joinpath("schemas", known.file).read_text("utf-8")
    assert result["schema"] == json.loads(text)
    assert result["format"] == known.format and result["version"] == known.version
    props = result["schema"].get("properties", {})
    if "format" in props and "const" in props["format"]:
        assert props["format"]["const"] == result["format"]
    else:
        assert result["format"] is None
    version = props.get("version") or props.get("schema_version") or {}
    if "const" in version:
        assert version["const"] == result["version"]
    assert result["schema"]["$schema"].startswith("https://json-schema.org/")


def test_the_schema_is_the_one_the_readers_validate_with(api11):
    from shape.design import design_input_schema
    from shape.generation.schema import json_schema
    from shape.project import schema as project_schema
    from shape.spec.model import model_schema

    assert api11.ok("format_schema", name="design-input")["schema"] == design_input_schema()
    assert api11.ok("format_schema", name="generation-schema")["schema"] == json_schema()
    assert api11.ok("format_schema", name="project")["schema"] == project_schema()
    assert api11.ok("format_schema", name="model")["schema"] == model_schema()


def test_a_schema_validates_what_its_reader_accepts(api11, tmp_path):
    from data_1_1 import retail_design

    from shape.schemacheck import validate

    schema = api11.ok("format_schema", name="design-input")["schema"]
    assert validate(retail_design(), schema) == []
    assert validate({"format": "shape-design"}, schema)


@pytest.mark.parametrize("name", ["", "nope", "Design-Input", "design_input", "../x", "project "])
def test_an_unknown_name_is_refused_with_the_list(api11, name):
    error = api11.fail("format_schema", "input.unknown_format", name=name)
    assert "design-input" in error["message"] and error["hint"]


def test_name_must_be_a_string(api11):
    api11.fail("format_schema", "usage.invalid_argument", name=3)
    api11.fail("format_schema", "usage.unknown_argument", format="x")
