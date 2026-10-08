"""#242: names that differ only in case are rejected where they become file names."""

from __future__ import annotations

import pytest

from shape.errors import ShapeError
from shape.generation.schema import GenSchema, GenSchemaError
from shape.registry.local import LocalRegistry, RegistryError


def _doc(*names):
    col = {"id": {"name": "id", "type": "integer", "generator": {"strategy": "sequence"}}}
    return {
        "schema_version": 1,
        "model": {"name": "m"},
        "generation": {},
        "tables": {n: {"name": n, "columns": col, "primary_key": ["id"]} for n in names},
        "relationships": [],
    }


def test_schema_rejects_case_twins():
    with pytest.raises(GenSchemaError, match="Orders.*orders|differ only in case"):
        GenSchema.from_dict(_doc("Orders", "orders"))
    GenSchema.from_dict(_doc("Orders", "Lines"))


def test_local_registry_rejects_case_twins(tmp_path):
    reg = LocalRegistry(tmp_path)
    reg.commit("Sales", "a")
    reg.commit("Sales", "b")
    with pytest.raises(RegistryError, match="differs only in case"):
        reg.commit("sales", "c")


def test_profile_registry_rejects_case_twins(tmp_path):
    from shape.registry.profiles import ProfileRegistry, ProfileRegistryError

    reg = ProfileRegistry(tmp_path)
    reg._write_index({})
    p = tmp_path / "crm" / "Customers"
    p.mkdir(parents=True)
    (p / "x.shape").write_bytes(b"")
    with pytest.raises(ProfileRegistryError, match="differs only in case"):
        reg._check_case(["crm/customers/x"])
    with pytest.raises(ProfileRegistryError, match="differ only in case"):
        reg._check_case(["a/T/x", "a/t/x"])
    reg._check_case(["crm/Customers/x"])
    assert issubclass(ProfileRegistryError, ShapeError)
