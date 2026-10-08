"""The telecom domain: plans, device models, subscribers, service lines, usage records, billing,
payments, network events and churn indicators (9 tables), with the reference data it draws from
(device models, network event types, plan types; the ZIP locations are the retail domain's)."""

from __future__ import annotations

from shape_domains._packaged import PackagedDomain

SHAPE_API = "1.0"


class TelecomDomain(PackagedDomain):
    """``shape.domains`` entry ``telecom``."""

    name = "telecom"
    description = (
        "Telecom domain with subscribers, service lines, usage records, billing, and churn"
    )
    datasets = ("device_models", "network_event_types", "plan_types")
    borrowed = {"us_zip_locations": ("retail", "us_zip_locations")}
