"""Delta registrations, including the layout rejected by the Owner's Lakehouse."""

import pyarrow as pa
import pytest
from shape_fabric.source import resolve
from shape_fabric.testing import FakeLakehouse

from shape.builtins.sinks.delta import _location


@pytest.mark.parametrize("enabled,schema", [(False, None), (True, "dbo"), (True, "sales")])
def test_the_delta_sink_registers_a_prefixed_table_in_each_lakehouse_kind(enabled, schema):
    lakehouse = FakeLakehouse(schema_enabled=enabled, schemas=("dbo", "sales"))
    options = {"token": "test-token", "table_prefix": "shape_", "schema_enabled": enabled}
    if schema:
        options["schema_name"] = schema
    location, _ = _location(
        "delta+abfss://ws@onelake.dfs.fabric.microsoft.com/lh.Lakehouse/Tables", "orders", options
    )
    lakehouse.write_deltalake(location, pa.table({"id": [1, 2]}))
    assert lakehouse.tables == {(schema, "shape_orders"): 2}
    assert lakehouse.unidentified == []
    suffix = f"{schema}/shape_orders" if schema else "shape_orders"
    assert resolve(f"onelake://ws/lh/Tables/{suffix}") == ("delta", "delta+" + location)


def test_a_schema_enabled_lakehouse_does_not_register_a_classic_layout():
    lakehouse = FakeLakehouse(schema_enabled=True)
    lakehouse.write_deltalake(
        "abfss://ws@onelake.dfs.fabric.microsoft.com/lh.Lakehouse/Tables/orders",
        pa.table({"id": [1]}),
    )
    assert lakehouse.tables == {}
    assert len(lakehouse.unidentified) == 1


def test_the_table_folder_helper_can_name_an_existing_lakehouse_schema():
    from shape_fabric.onelake import tables_folder

    assert (
        tables_folder("onelake://ws/lh/Files", "orders", schema_name="dbo")
        == "onelake://ws/lh/Tables/dbo/orders"
    )
    assert tables_folder("onelake://ws/lh/Files", "orders") == "onelake://ws/lh/Tables/orders"
