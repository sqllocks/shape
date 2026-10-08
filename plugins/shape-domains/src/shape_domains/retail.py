"""The retail domain: customers, addresses, products, stores, promotions, orders, order lines and
returns (9 tables, 3NF; a star schema too), with the reference data it draws from (product
categories, product and promotion names, 40,977 US ZIP locations). Other domains borrow the ZIP
locations from here."""

from __future__ import annotations

import copy
import json
from functools import cache
from importlib import resources
from typing import Any

from shape_domains._packaged import PackagedDomain

SHAPE_API = "1.0"


@cache
def _transforms() -> dict[str, Any]:
    text = (
        resources.files("shape_domains").joinpath("data/retail/transforms.json").read_text("utf-8")
    )
    document: dict[str, Any] = json.loads(text)
    return document


class RetailDomain(PackagedDomain):
    """``shape.domains`` entry ``retail``."""

    name = "retail"
    description = "Retail / E-Commerce domain with customers, products, orders, and returns"
    datasets = ("categories", "product_names", "promo_names", "us_zip_locations")

    def star_map(self) -> dict[str, Any]:
        """How ``shape transform star`` reshapes this domain's tables into dimensions and facts."""
        star: dict[str, Any] = copy.deepcopy(_transforms()["star"])
        return star

    def cdm_entities(self) -> dict[str, str]:
        """Table name to CDM entity name, for ``shape transform cdm``."""
        entities: dict[str, str] = dict(_transforms()["cdm_entities"])
        return entities
