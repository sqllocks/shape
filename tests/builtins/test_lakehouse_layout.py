"""The Delta sink writes ``Tables/<schema>/<table>`` to a schema-enabled Lakehouse and
``Tables/<table>`` to a classic one, and never guesses which it is talking to."""

import pytest

from shape.builtins.sinks.delta import _location
from shape.integrations.fabric.lakehouse_kind import LakehouseKindError, resolve_layout

WS = "a6ed8572-5faa-4a84-89cf-ec4def7c56cd"
SCHEMA_LH = "2286b792-8ef8-4ea9-aff7-22e75243c5bc"
CLASSIC_LH = "045efe09-1218-453b-aeae-ec4def7c56cd"
HOST = "onelake.dfs.fabric.microsoft.com"


def base(lakehouse):
    return f"delta+abfss://{WS}@{HOST}/{lakehouse}/Tables"


def where(lakehouse, table="orders", **options):
    return _location(base(lakehouse), table, {"account_key": "k", **options})[0]


def fabric(calls=None):
    """A Fabric REST double: the schema-enabled Lakehouse reports ``defaultSchema``."""

    def fetch(path, _body=None):
        if calls is not None:
            calls.append(path)
        if path == f"/v1/workspaces/{WS}/lakehouses/{SCHEMA_LH}":
            return {"id": SCHEMA_LH, "properties": {"defaultSchema": "dbo"}}
        if path == f"/v1/workspaces/{WS}/lakehouses/{CLASSIC_LH}":
            return {"id": CLASSIC_LH, "properties": {"oneLakeTablesPath": "x"}}
        raise OSError("404 Not Found")

    return fetch


def test_a_schema_enabled_lakehouse_is_detected_and_gets_dbo():
    got = where(SCHEMA_LH, fabric_fetch=fabric(), table_prefix="shape_")
    assert got == f"abfss://{WS}@{HOST}/{SCHEMA_LH}/Tables/dbo/shape_orders"


def test_the_schema_is_user_selectable():
    got = where(SCHEMA_LH, fabric_fetch=fabric(), schema_name="sales")
    assert got.endswith(f"/{SCHEMA_LH}/Tables/sales/orders")


def test_a_classic_lakehouse_is_detected_and_keeps_the_flat_layout():
    got = where(CLASSIC_LH, fabric_fetch=fabric(), table_prefix="shape_")
    assert got == f"abfss://{WS}@{HOST}/{CLASSIC_LH}/Tables/shape_orders"


def test_a_schema_on_a_classic_lakehouse_is_an_error_not_ignored():
    with pytest.raises(LakehouseKindError, match="not schema-enabled"):
        where(CLASSIC_LH, fabric_fetch=fabric(), schema_name="dbo")


@pytest.mark.parametrize("value,tail", [(True, "Tables/dbo/orders"), ("false", "Tables/orders")])
def test_an_explicit_setting_needs_no_lookup(value, tail):
    calls = []
    got = where(SCHEMA_LH, fabric_fetch=fabric(calls), schema_enabled=value)
    assert got.endswith(f"/{SCHEMA_LH}/{tail}") and calls == []


def test_an_unknown_kind_is_an_error_naming_the_setting():
    with pytest.raises(LakehouseKindError, match="schema_enabled=true"):
        where(SCHEMA_LH)  # no credential, no setting: never guess


def test_a_failed_lookup_is_an_error_not_a_guess():
    with pytest.raises(LakehouseKindError, match="Fabric did not say"):
        where("11111111-1111-1111-1111-111111111111", fabric_fetch=fabric())


def test_a_lakehouse_named_rather_than_identified_needs_the_setting():
    uri = f"delta+abfss://shape-test@{HOST}/shape_lh.Lakehouse/Tables"
    with pytest.raises(LakehouseKindError, match="not its ids"):
        _location(uri, "orders", {"account_key": "k", "fabric_fetch": fabric()})
    got, _ = _location(uri, "orders", {"account_key": "k", "schema_enabled": True})
    assert got.endswith("/shape_lh.Lakehouse/Tables/dbo/orders")


def test_resolve_layout_asks_fabric_with_the_credential_once():
    calls = []

    class Credential:
        def get_token(self, scope):
            calls.append(scope)
            raise OSError("offline")

    with pytest.raises(LakehouseKindError):
        resolve_layout(WS, SCHEMA_LH, credential=Credential())
    assert calls == ["https://api.fabric.microsoft.com/.default"]


def test_a_schema_outside_a_lakehouse_tables_root_is_refused():
    with pytest.raises(ValueError, match="OneLake Lakehouse"):
        _location(
            "delta+abfss://acct.dfs.core.windows.net".replace("acct", "c@acct") + "/data",
            "t",
            {"account_key": "k", "schema_name": "dbo"},
        )
