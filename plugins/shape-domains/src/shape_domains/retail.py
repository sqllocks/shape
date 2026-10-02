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
from shape_domains._packaged import _FILES, is_validated, reference_table, schema_document

SHAPE_API = "1.0"

_PACKAGE = "shape_domains"
_DATASETS = ("categories", "product_names", "promo_names", "us_zip_locations")


@cache
def _reference_data() -> dict[str, pa.Table]:
    return {name: reference_table("retail", name) for name in _DATASETS}


@cache
def _transforms() -> dict[str, Any]:
    text = resources.files(_PACKAGE).joinpath("data/retail/transforms.json").read_text("utf-8")
    document: dict[str, Any] = json.loads(text)
    return document


class RetailDomain:
    """``shape.domains`` entry ``retail``."""

    name = "retail"
    modes = ("3nf", "star")

    def definition(self, mode: str = "3nf") -> DomainDefinition:
        if mode not in _FILES:
            raise ValueError(f"retail has no {mode!r} mode (3nf, star)")
        schema = schema_document("retail", mode)
        return DomainDefinition(
            schema=schema,
            reference_data=_reference_data(),
            scale_presets={k: dict(v) for k, v in schema["generation"]["scales"].items()},
            validated=is_validated("retail", mode),
        )

    def star_map(self) -> dict[str, Any]:
        """How ``shape transform star`` reshapes this domain's tables into dimensions and facts."""
        star: dict[str, Any] = _transforms()["star"]
        return star

    def cdm_entities(self) -> dict[str, str]:
        """Table name to CDM entity name, for ``shape transform cdm``."""
        entities: dict[str, str] = _transforms()["cdm_entities"]
        return entities
