"""OneLake paths: parsing, the COPY INTO form, the landing-zone layout and path safety."""

import pytest
from shape_fabric import onelake
from shape_fabric.source import LakehouseSource, resolve

from shape.errors import ShapeError

WS = "8b6a0f7e-1c2d-4e3f-9a4b-5c6d7e8f9a0b"
LH = "1f2e3d4c-5b6a-4978-8695-a4b3c2d1e0f9"
HOST = "onelake.dfs.fabric.microsoft.com"


def test_parse_the_long_form_and_build_both_urls():
    p = onelake.parse(f"abfss://{WS}@{HOST}/{LH}/Files/landing/x")
    assert (p.workspace, p.item, p.path) == (WS, LH, "Files/landing/x")
    assert p.abfss() == f"abfss://{WS}@{HOST}/{LH}/Files/landing/x"
    # COPY INTO takes https with the workspace as the first path segment, not abfss
    assert p.https() == f"https://{HOST}/{WS}/{LH}/Files/landing/x"
    assert p.section == "Files"


def test_short_form_names_get_the_lakehouse_type_and_guids_do_not():
    assert onelake.parse("onelake://Analytics/Sales/Tables/orders").item == "Sales.Lakehouse"
    assert onelake.parse(f"onelake://Analytics/{LH}/Files").item == LH
    assert onelake.parse("onelake://Analytics/Sales.Warehouse/Tables/t").item == "Sales.Warehouse"
    assert (
        onelake.to_abfss("onelake://Analytics/Sales/Files/raw")
        == f"abfss://Analytics@{HOST}/Sales.Lakehouse/Files/raw"
    )


@pytest.mark.parametrize(
    "uri",
    [
        "abfss://c@acct.dfs.core.windows.net/p",  # ADLS, not OneLake
        f"abfss://{WS}@{HOST}",  # no item
        "onelake://ws",  # no lakehouse
        "https://x/y",
    ],
)
def test_not_onelake_uris_are_shape_errors(uri):
    with pytest.raises(ShapeError):
        onelake.parse(uri)


def test_landing_zone_layout():
    base = f"abfss://{WS}@{HOST}/{LH}/Files"
    assert onelake.landing_zone(base, "retail", "order", "2026-02-03", 4) == (
        f"{base}/landing/retail/order/dt=2026-02-03/hour=04"
    )
    assert onelake.manifest(base, "retail", "order", "2026-02-03").endswith(
        "/landing/retail/order/_control/manifest_2026-02-03.json"
    )
    assert onelake.done_flag(base, "retail", "order", "2026-02-03").endswith(
        "/_control/_SUCCESS_2026-02-03"
    )
    assert onelake.quarantine(base, "retail", "run1").endswith("/quarantine/retail/run1")
    assert (
        onelake.landing_zone("/tmp/x", "d", "e", "2026-01-01") == "/tmp/x/landing/d/e/dt=2026-01-01"
    )


@pytest.mark.parametrize("bad", ["..", "a/../b", "a\\b", "a\x00b", "a?b", "a#b", ""])
def test_a_name_that_could_leave_the_folder_is_refused(bad):
    with pytest.raises(ShapeError):
        onelake.landing_zone("/tmp/x", bad, "e", "2026-01-01")


def test_bad_dates_and_hours_are_refused():
    with pytest.raises(ShapeError):
        onelake.landing_zone("/tmp/x", "d", "e", "2026-1-1")
    with pytest.raises(ShapeError):
        onelake.landing_zone("/tmp/x", "d", "e", "2026-01-01", 24)


def test_tables_folder_is_the_sibling_of_files():
    assert onelake.tables_folder("/lh/Files", "t") == "/lh/Tables/t"
    with pytest.raises(ShapeError):
        onelake.tables_folder("/lh/Other", "t")


def test_source_turns_the_short_form_into_the_core_sources():
    assert resolve("onelake://A/Sales/Tables/orders") == (
        "delta",
        f"delta+abfss://A@{HOST}/Sales.Lakehouse/Tables/orders",
    )
    assert resolve("onelake://A/Sales/Tables/dbo/orders")[0] == "delta"  # schema-enabled lakehouse
    assert resolve("onelake://A/Sales/Files/raw/*.parquet") == (
        "abfss",
        f"abfss://A@{HOST}/Sales.Lakehouse/Files/raw/*.parquet",
    )
    for bad in ("onelake://A/Sales/Tables", "onelake://A/Sales", "onelake://A/Sales/Other/x"):
        with pytest.raises(ShapeError):
            resolve(bad)


def test_source_only_claims_onelake_uris():
    src = LakehouseSource()
    assert src.can_open("onelake://A/B/Tables/t")
    assert not src.can_open("abfss://c@h/p") and not src.can_open("/tmp/x.csv")
