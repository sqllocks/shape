"""The marketing domain (10 tables), with the reference data it draws from (campaign types,
industry names, lead sources)."""

from __future__ import annotations

from shape_domains._packaged import PackagedDomain

SHAPE_API = "1.0"


class MarketingDomain(PackagedDomain):
    """``shape.domains`` entry ``marketing``."""

    name = "marketing"
    description = "Marketing domain with campaigns, contacts, leads, opportunities, and conversions"
    datasets = ("campaign_types", "industry_names", "lead_sources")
