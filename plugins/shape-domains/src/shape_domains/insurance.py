"""The insurance domain: agents, policyholders, policy types, policies, coverages, claims, claim and premium
payments and underwriting (9 tables),
with the reference data it draws from (claim categories, peril types and policy types)."""

from __future__ import annotations

from shape_domains._packaged import PackagedDomain

SHAPE_API = "1.0"


class InsuranceDomain(PackagedDomain):
    """``shape.domains`` entry ``insurance``."""

    name = "insurance"
    datasets = ("claim_categories", "peril_types", "policy_types")
