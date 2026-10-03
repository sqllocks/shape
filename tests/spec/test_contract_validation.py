"""Regression tests for AUD-design: a contract is checked against ``shape-v1.schema.json`` and
its mandatory capabilities (SHAPE_1_0 rule 1)."""

import pytest

from shape.spec import ShapeContract


def test_an_unknown_mandatory_capability_is_refused():  # issue 383
    with pytest.raises(ValueError, match="future/9"):
        ShapeContract.from_dict({"name": "x", "fields": [], "mandatory_capabilities": ["future/9"]})


def test_known_mandatory_and_unknown_optional_capabilities_are_accepted():
    c = ShapeContract.from_dict(
        {
            "name": "x",
            "fields": [],
            "mandatory_capabilities": ["core/1"],
            "optional_capabilities": ["future/9"],
        }
    )
    assert c.name == "x"


@pytest.mark.parametrize(
    "doc",
    [
        {"name": 123, "fields": []},
        {"name": "", "fields": []},
        {"name": "x", "version": True},
        {"name": "x", "version": 1.9},
        {"name": "x", "version": "1"},
        {"name": "x", "metadata": [1]},
        {"name": "x", "fidelity": "tin"},
        {"name": "x", "fields": [{"name": "a", "kind": "int", "bogus": 1}]},
        {"name": "x", "fields": "ab"},
        {"name": "x", "unknown_key": 1},
    ],
)
def test_a_document_outside_the_schema_is_refused_with_its_path(doc):  # issue 383
    with pytest.raises(ValueError, match=r"\$"):
        ShapeContract.from_dict(doc)


def test_a_newer_contract_version_says_to_upgrade():
    with pytest.raises(ValueError, match="upgrade Shape"):
        ShapeContract.from_dict({"name": "x", "version": 2})


def test_a_missing_name_and_a_duplicate_field_are_named():
    with pytest.raises(ValueError, match="name"):
        ShapeContract.from_dict({"fields": []})
    with pytest.raises(ValueError, match="'a'"):
        ShapeContract.from_dict(
            {"name": "x", "fields": [{"name": "a", "kind": "i"}, {"name": "a", "kind": "i"}]}
        )
    with pytest.raises(ValueError, match="object"):
        ShapeContract.from_dict([])  # type: ignore[arg-type]


def test_defaults_still_fill_missing_version_fidelity_fields():
    c = ShapeContract.from_dict({"name": "x"})
    assert (c.version, c.fidelity, c.fields) == (1, "gold", ())


# ---- issue 393 ------------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["core/1", None, [1], {"core/1": 1}])
def test_check_capabilities_refuses_a_value_that_is_not_a_list_of_strings(value):
    from shape.spec import check_capabilities

    with pytest.raises(ValueError, match="mandatory_capabilities.*list of strings"):
        check_capabilities({"mandatory_capabilities": value})


def test_check_capabilities_accepts_a_tuple():
    from shape.spec import check_capabilities

    assert check_capabilities({"mandatory_capabilities": ("core/1",)}).compatible


def test_migrating_keys_that_name_the_same_column_is_refused():
    from shape.spec.migrate import migrate_capture_v1
    from shape.spec.model import ModelError

    # built, not written as one literal: tests/artifact scans literal v1 shapes as valid fixtures
    columns: dict[object, dict[str, object]] = {1: {}}
    columns["1"] = {}
    with pytest.raises(ModelError, match="'1'"):
        migrate_capture_v1({"rows": 1, "columns": columns})
