"""The supply_chain domain: warehouses, suppliers, materials, purchase orders and lines, inventory,
shipments and their events, quality inspections and demand forecasts (10 tables), with the
reference data it draws from (carrier names, material categories, shipping methods; the ZIP
locations are the retail domain's)."""

from __future__ import annotations

from shape_domains._packaged import PackagedDomain

SHAPE_API = "1.0"


class SupplyChainDomain(PackagedDomain):
    """``shape.domains`` entry ``supply_chain``."""

    name = "supply_chain"
    datasets = ("carrier_names", "material_categories", "shipping_methods")
    borrowed = {"us_zip_locations": ("retail", "us_zip_locations")}
