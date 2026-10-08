"""The insurance domain: agents, policyholders, policy types, policies, coverages, claims, claim
and premium payments and underwriting (9 tables), with the reference data it draws from (claim
categories, peril types and policy types; the ZIP locations are the retail domain's)."""

from __future__ import annotations

from shape_domains._packaged import PackagedDomain

SHAPE_API = "1.0"


class InsuranceDomain(PackagedDomain):
    """``shape.domains`` entry ``insurance``."""

    name = "insurance"
    description = "Insurance domain with policies, claims, underwriting, and premium management"
    datasets = ("claim_categories", "peril_types", "policy_types")
    borrowed = {"us_zip_locations": ("retail", "us_zip_locations")}
