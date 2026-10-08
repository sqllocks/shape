"""The retail domain plugin: its schemas, reference data and transforms."""

from __future__ import annotations

import pyarrow as pa
import pytest
from shape_domains.retail import RetailDomain

TABLES = [
    "customer",
    "address",
    "product_category",
    "product",
    "store",
    "promotion",
    "order",
    "order_line",
    "return",
]


@pytest.mark.parametrize("mode", ["3nf", "star"])
def test_definition_has_the_nine_tables_scales_and_reference_data(mode):
    d = RetailDomain().definition(mode)
    assert list(d.schema["tables"]) == TABLES
    assert {"small", "medium", "large", "xlarge"} <= set(d.scale_presets)
    assert d.scale_presets == d.schema["generation"]["scales"]
    sizes = {name: table.num_rows for name, table in d.reference_data.items()}
    assert sizes["us_zip_locations"] == 40977
    assert set(sizes) == {"categories", "product_names", "promo_names", "us_zip_locations"}
    assert all(isinstance(t, pa.Table) for t in d.reference_data.values())


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValueError, match=r"no 'snowflake' mode \(3nf, star\)"):
        RetailDomain().definition("snowflake")


def test_star_map_and_cdm_entities():
    r = RetailDomain()
    assert set(r.star_map()) == {"dimensions", "facts"}
    assert list(r.cdm_entities()) == TABLES


def test_changing_a_returned_definition_does_not_change_later_loads():
    # Issue #343: definition(), star_map() and cdm_entities() returned the cached dicts, so a
    # caller's change altered every later load of the domain in the process.
    first = RetailDomain().definition()
    small = dict(first.schema["generation"]["scales"]["small"])
    first.schema["generation"]["scales"]["small"]["customer"] = 1
    first.scale_presets["small"]["customer"] = 2
    first.reference_data.pop("categories")
    RetailDomain().star_map().clear()
    RetailDomain().cdm_entities().clear()

    again = RetailDomain().definition()
    assert again.schema["generation"]["scales"]["small"] == small
    assert again.scale_presets["small"] == small
    assert "categories" in again.reference_data
    assert set(RetailDomain().star_map()) == {"dimensions", "facts"}
    assert list(RetailDomain().cdm_entities()) == TABLES
