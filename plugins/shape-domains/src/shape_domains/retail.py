"""The retail domain: customers, addresses, products, stores, promotions, orders, order lines and
returns (9 tables, 3NF), with the reference data it draws from (product categories, product and
promotion names, 40,977 US ZIP locations)."""

from __future__ import annotations

import json
from functools import cache
from importlib import resources
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.plugins.api.v1 import DomainDefinition

SHAPE_API = "1.0"

_PACKAGE = "shape_domains"
_DATASETS = ("categories", "product_names", "promo_names", "us_zip_locations")


@cache
def _schema() -> dict[str, Any]:
    text = resources.files(_PACKAGE).joinpath("data/retail/schema.json").read_text("utf-8")
    document: dict[str, Any] = json.loads(text)
    return document


@cache
def _reference_data() -> dict[str, pa.Table]:
    root = resources.files(_PACKAGE).joinpath("data/retail/reference")
    out: dict[str, pa.Table] = {}
    for name in _DATASETS:
        with root.joinpath(f"{name}.arrow").open("rb") as handle:
            out[name] = pa.ipc.open_file(handle).read_all()
    return out


class RetailDomain:
    """``shape.domains`` entry ``retail``."""

    name = "retail"

    def definition(self) -> DomainDefinition:
        schema = _schema()
        return DomainDefinition(
            schema=schema,
            reference_data=_reference_data(),
            scale_presets={k: dict(v) for k, v in schema["generation"]["scales"].items()},
        )
