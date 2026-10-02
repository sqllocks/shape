"""The real_estate domain: neighborhoods, agents, properties, listings, showings, offers, transactions,
inspections and appraisals (9 tables), with the reference data it draws from (inspection items,
neighborhoods, property types; the ZIP locations are the retail domain's)."""

from __future__ import annotations

from shape_domains._packaged import PackagedDomain

SHAPE_API = "1.0"


class RealEstateDomain(PackagedDomain):
    """``shape.domains`` entry ``real_estate``."""

    name = "real_estate"
    datasets = ("inspection_items", "neighborhoods", "property_types")
    borrowed = {"us_zip_locations": ("retail", "us_zip_locations")}
