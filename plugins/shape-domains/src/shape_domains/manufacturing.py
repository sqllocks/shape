"""The manufacturing domain (9 tables), with the reference data it draws from (defect codes,
material types, operation types)."""

from __future__ import annotations

from shape_domains._packaged import PackagedDomain

SHAPE_API = "1.0"


class ManufacturingDomain(PackagedDomain):
    """``shape.domains`` entry ``manufacturing``."""

    name = "manufacturing"
    description = (
        "Manufacturing domain with production lines, work orders, quality control, and equipment"
    )
    datasets = ("defect_codes", "material_types", "operation_types")
