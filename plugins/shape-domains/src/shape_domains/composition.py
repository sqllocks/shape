"""How the domains combine: the shared-entity tables and the named composite presets.

A composite (``shape composite``) links domains through *shared entities*: a person, a location
and an organisation. Each concept lists the table that plays it in each domain; the first listed
domain that takes part in a composite is the concept's primary, and the others get a bridge column
that points at it. The presets name six combinations.
"""

from __future__ import annotations

from shape.generation.composite import Composition, EntityMapping, Preset

SHAPE_API = "1.0"


def _m(domain: str, table: str, pk: str) -> EntityMapping:
    return EntityMapping(domain, table, pk)


MAPPINGS: dict[str, tuple[EntityMapping, ...]] = {
    "person": (
        _m("retail", "customer", "customer_id"),
        _m("hr", "employee", "employee_id"),
        _m("financial", "customer", "customer_id"),
        _m("healthcare", "patient", "patient_id"),
        _m("insurance", "policyholder", "policyholder_id"),
        _m("education", "student", "student_id"),
        _m("marketing", "contact", "contact_id"),
        _m("telecom", "subscriber", "subscriber_id"),
        _m("real_estate", "agent", "agent_id"),
    ),
    "location": (
        _m("retail", "store", "store_id"),
        _m("hr", "department", "department_id"),
        _m("financial", "branch", "branch_id"),
        _m("healthcare", "facility", "facility_id"),
        _m("manufacturing", "production_line", "line_id"),
        _m("iot", "location", "location_id"),
        _m("supply_chain", "warehouse", "warehouse_id"),
        _m("real_estate", "neighborhood", "neighborhood_id"),
    ),
    "organization": (
        _m("hr", "department", "department_id"),
        _m("financial", "branch", "branch_id"),
        _m("healthcare", "provider", "provider_id"),
        _m("manufacturing", "production_line", "line_id"),
        _m("education", "department", "department_id"),
        _m("supply_chain", "supplier", "supplier_id"),
        _m("insurance", "agent", "agent_id"),
        _m("marketing", "industry", "industry_id"),
        _m("capital_markets", "company", "ticker"),
    ),
}

PRESETS: tuple[Preset, ...] = (
    Preset(
        "enterprise",
        "Enterprise dataset combining retail, HR, and financial domains",
        ("retail", "hr", "financial"),
        {
            "person": {
                "primary": "hr.employee",
                "links": {"retail": "customer.customer_id", "financial": "account.account_id"},
            }
        },
    ),
    Preset(
        "healthcare_system",
        "Healthcare system with insurance and HR",
        ("healthcare", "insurance", "hr"),
        {
            "person": {
                "primary": "hr.employee",
                "links": {"healthcare": "patient.patient_id", "insurance": "policy.policy_id"},
            }
        },
    ),
    Preset(
        "smart_factory",
        "Smart factory combining manufacturing, IoT, and supply chain",
        ("manufacturing", "iot", "supply_chain"),
    ),
    Preset(
        "digital_commerce",
        "Digital commerce with retail, marketing, and financial data",
        ("retail", "marketing", "financial"),
    ),
    Preset("campus", "University campus combining education and HR", ("education", "hr")),
    Preset(
        "telecom_bundle",
        "Telecom provider with marketing and billing",
        ("telecom", "marketing", "financial"),
    ),
)


def composition() -> Composition:
    """What this package offers to ``shape composite`` and ``shape presets``."""
    return Composition(PRESETS, MAPPINGS)
