"""The deliberate corrections of the shipped domain schemas to the baseline's dumps (ISS-gen).

``export_domains.py`` writes each domain's ``schema.json`` and ``schema_star.json`` from the
baseline's dumps and then applies, for both modes, what is listed here: generators replaced
(``GENERATORS``), columns added (``ADDED_COLUMNS``) and business rules given real rule text or
added (``RULES``). Everything else stays the baseline's, which the product tests prove
(``tests/generation/test_domains_p601b.py``, ``tests/generation/test_composite_p601e.py``).

Each correction fixes a defect that harms trust in the data:

* **Empty business rules.** The baseline dumps some rules with ``"rule": ""`` and a type the rules
  engine does not evaluate (``temporal_order``, ``column_comparison``), so nothing enforced or
  reported them. They get rule text the engine checks and repairs.
* **Insurance dates.** Claim, payment and underwriting dates were independent draws over the whole
  date range, so half of the claims happened before their policy began. They now follow their
  parent dates: a claim's incident falls in its policy's term, filing follows the incident after a
  log-normal lag (median about five days), a claim payment follows the filing, a premium payment
  falls in the term and underwriting comes before the policy takes effect.
* **Insurance amounts.** ``claim.total_paid`` sums the claim's payments, and the rule
  ``paid_within_claim_amount`` keeps every claim at least as large as what was paid on it.
* **Insurance policy lines.** ``policy_type.category`` was drawn independently of ``type_name``
  (a whole-life policy in the Auto category), and ``type_name`` was drawn with replacement, so the
  30-row catalogue repeated some types and left others out. Each type now appears once with its
  own line of business, which policies, coverages and claims carry as ``line_of_business`` and
  which decides a claim's cause and a coverage's type.

What the shipped strategies cannot express yet is in ``docs/plans/proposals/``: claim status by
claim age, and a policy term drawn from a set of lengths.

Standard library only.
"""

from __future__ import annotations

import copy
from typing import Any

# ---- insurance ------------------------------------------------------------------------------

# Each policy type of the ``policy_types`` dataset and the line of business it belongs to.
INSURANCE_LINES: dict[str, str] = {
    "Standard Auto": "Auto",
    "Preferred Auto": "Auto",
    "High-Risk Auto": "Auto",
    "Classic Car": "Auto",
    "Commercial Auto": "Commercial",
    "Homeowners HO-3": "Home",
    "Homeowners HO-5": "Home",
    "Condo HO-6": "Home",
    "Renters HO-4": "Home",
    "Mobile Home": "Home",
    "Term Life 10-Year": "Life",
    "Term Life 20-Year": "Life",
    "Term Life 30-Year": "Life",
    "Whole Life": "Life",
    "Universal Life": "Life",
    "Variable Life": "Life",
    "Individual Health": "Health",
    "Family Health": "Health",
    "Supplemental Health": "Health",
    "Dental": "Health",
    "Vision": "Health",
    "General Liability": "Commercial",
    "Professional Liability": "Commercial",
    "Workers Compensation": "Commercial",
    "Commercial Property": "Commercial",
    "Business Owners Policy": "Commercial",
    "Personal Umbrella": "Umbrella",
    "Commercial Umbrella": "Umbrella",
    "Flood Insurance": "Home",
    "Earthquake Insurance": "Home",
}

# The causes of a claim on each line. Auto, home, commercial and umbrella causes are the
# ``claim_categories`` names with their weights; life and health claims have causes of their own.
INSURANCE_CAUSES: dict[str, dict[str, float]] = {
    "Auto": {
        "Rear-End Collision": 2.2,
        "Side-Impact Collision": 1.5,
        "Single Vehicle Accident": 1.3,
        "Multi-Vehicle Accident": 1.0,
        "Theft - Vehicle": 0.8,
        "Vandalism": 0.7,
        "Wind/Hail Damage": 0.6,
        "Falling Object": 0.3,
        "Glass/Windshield": 0.8,
        "Uninsured Motorist": 0.6,
        "Hit and Run": 0.7,
    },
    "Home": {
        "Theft - Property": 0.9,
        "Vandalism": 0.7,
        "Wind/Hail Damage": 1.8,
        "Water Damage - Plumbing": 1.4,
        "Water Damage - Storm": 1.1,
        "Fire Damage": 0.9,
        "Smoke Damage": 0.5,
        "Lightning Strike": 0.4,
        "Falling Object": 0.6,
        "Slip and Fall": 1.2,
        "Dog Bite": 0.5,
        "Roof Damage": 1.0,
        "Foundation Damage": 0.4,
        "Pipe Burst/Freeze": 0.9,
        "Natural Disaster": 0.5,
    },
    "Commercial": {
        "Theft - Property": 0.9,
        "Vandalism": 0.7,
        "Wind/Hail Damage": 1.0,
        "Water Damage - Plumbing": 0.8,
        "Fire Damage": 0.9,
        "Smoke Damage": 0.3,
        "Slip and Fall": 1.5,
        "Equipment Breakdown": 1.0,
        "Rear-End Collision": 0.6,
        "Multi-Vehicle Accident": 0.5,
        "Workplace Injury": 1.2,
        "Professional Error": 0.6,
    },
    "Umbrella": {"Multi-Vehicle Accident": 1.0, "Slip and Fall": 1.0, "Dog Bite": 0.6},
    "Health": {
        "Medical Emergency": 1.0,
        "Hospital Admission": 0.8,
        "Outpatient Procedure": 1.2,
        "Prescription": 1.5,
        "Dental Treatment": 0.6,
        "Vision Care": 0.4,
    },
    "Life": {"Death - Natural Causes": 1.0, "Death - Accident": 0.3, "Terminal Illness": 0.15},
}

# The coverages a policy of each line has (the baseline's labels, plus the death benefits of a
# life policy).
INSURANCE_COVERAGES: dict[str, dict[str, float]] = {
    "Auto": {
        "Liability": 0.3,
        "Collision": 0.25,
        "Comprehensive": 0.2,
        "Medical": 0.1,
        "Uninsured": 0.15,
    },
    "Home": {"Property": 0.55, "Liability": 0.3, "Medical": 0.15},
    "Commercial": {"Property": 0.35, "Liability": 0.45, "Medical": 0.1, "Collision": 0.1},
    "Umbrella": {"Liability": 1.0},
    "Health": {"Medical": 1.0},
    "Life": {"Death Benefit": 0.8, "Accidental Death": 0.2},
}

_BASELINE_CATEGORY = {
    "Auto": 0.25,
    "Home": 0.2,
    "Life": 0.18,
    "Health": 0.15,
    "Commercial": 0.15,
    "Umbrella": 0.07,
}
_BASELINE_COVERAGE = {
    "Liability": 0.25,
    "Collision": 0.18,
    "Comprehensive": 0.17,
    "Medical": 0.15,
    "Property": 0.15,
    "Uninsured": 0.1,
}
# The ``claim_categories`` dataset: what a claim's cause was drawn from, and what a claim whose
# line has no entry above still draws from.
_BASELINE_CAUSE = {
    "Rear-End Collision": 2.2,
    "Side-Impact Collision": 1.5,
    "Single Vehicle Accident": 1.3,
    "Multi-Vehicle Accident": 1.0,
    "Theft - Vehicle": 0.8,
    "Theft - Property": 0.9,
    "Vandalism": 0.7,
    "Wind/Hail Damage": 1.8,
    "Water Damage - Plumbing": 1.4,
    "Water Damage - Storm": 1.1,
    "Fire Damage": 0.9,
    "Smoke Damage": 0.5,
    "Lightning Strike": 0.4,
    "Falling Object": 0.6,
    "Slip and Fall": 1.2,
    "Dog Bite": 0.5,
    "Roof Damage": 1.0,
    "Foundation Damage": 0.4,
    "Pipe Burst/Freeze": 0.9,
    "Glass/Windshield": 0.8,
    "Medical Emergency": 1.0,
    "Uninsured Motorist": 0.6,
    "Hit and Run": 0.7,
    "Natural Disaster": 0.5,
    "Equipment Breakdown": 0.4,
}


def _after(source: str, via: str, distribution: str, **params: float) -> dict[str, Any]:
    """A ``derived`` date: ``source`` (a column of this table, or ``table.column`` read through
    ``via``) plus a whole number of days."""
    gen: dict[str, Any] = {"strategy": "derived", "source": source}
    if via:
        gen["via"] = via
    gen.update({"rule": "add_days", "params": {"distribution": distribution, **params}})
    return gen


def _line_of_business(source_table: str, source_column: str, via: str) -> dict[str, Any]:
    return {
        "name": "line_of_business",
        "type": "string",
        "generator": {
            "strategy": "lookup",
            "source_table": source_table,
            "source_column": source_column,
            "via": via,
        },
        "nullable": False,
        "null_rate": 0.0,
        "max_length": 20,
        "precision": None,
        "scale": None,
    }


# (domain, table, column) -> the generator that replaces the baseline's
GENERATORS: dict[tuple[str, str, str], dict[str, Any]] = {
    ("insurance", "policy_type", "type_name"): {
        "strategy": "record_sample",
        "dataset": "policy_types",
        "field": "name",
        "unique": True,
    },
    ("insurance", "policy_type", "category"): {
        "strategy": "conditional_table",
        "source_column": "type_name",
        "table": {name: {line: 1.0} for name, line in INSURANCE_LINES.items()},
        "values": _BASELINE_CATEGORY,
    },
    ("insurance", "coverage", "coverage_type"): {
        "strategy": "conditional_table",
        "source_column": "line_of_business",
        "table": INSURANCE_COVERAGES,
        "values": _BASELINE_COVERAGE,
    },
    # In the policy's term (it lasts 365 days).
    ("insurance", "claim", "incident_date"): _after(
        "policy.effective_date", "policy_id", "uniform", min=0, max=364
    ),
    # Median e**1.6, about five days; one claim in twenty is filed after about 25 days.
    ("insurance", "claim", "filed_date"): _after(
        "incident_date", "", "log_normal", mean=1.6, sigma=1.0, min=0, max=365
    ),
    ("insurance", "claim", "cause"): {
        "strategy": "conditional_table",
        "source_column": "line_of_business",
        "table": INSURANCE_CAUSES,
        "values": _BASELINE_CAUSE,
    },
    # Median e**3.4, about 30 days after the claim is filed.
    ("insurance", "claim_payment", "payment_date"): _after(
        "claim.filed_date", "claim_id", "log_normal", mean=3.4, sigma=0.8, min=1, max=730
    ),
    ("insurance", "premium_payment", "payment_date"): _after(
        "policy.effective_date", "policy_id", "uniform", min=0, max=364
    ),
    ("insurance", "underwriting", "assessed_date"): _after(
        "policy.effective_date", "policy_id", "uniform", min=-60, max=-1
    ),
}

# (domain, table, column) -> (the column it goes before, or None for the end; the column)
ADDED_COLUMNS: dict[tuple[str, str, str], tuple[str | None, dict[str, Any]]] = {
    ("insurance", "policy", "line_of_business"): (
        None,
        _line_of_business("policy_type", "category", "policy_type_id"),
    ),
    ("insurance", "coverage", "line_of_business"): (
        "coverage_type",
        _line_of_business("policy", "line_of_business", "policy_id"),
    ),
    ("insurance", "claim", "line_of_business"): (
        "cause",
        _line_of_business("policy", "line_of_business", "policy_id"),
    ),
    ("insurance", "claim", "total_paid"): (
        None,
        {
            "name": "total_paid",
            "type": "float",
            "generator": {
                "strategy": "computed",
                "rule": "sum_children",
                "child_table": "claim_payment",
                "child_column": "payment_amount",
            },
            "nullable": False,
            "null_rate": 0.0,
            "max_length": None,
            "precision": None,
            "scale": None,
        },
    ),
}


def _rule(
    type_: str, rule: str, table: str | None = None, via: str | None = None
) -> dict[str, Any]:
    return {"type": type_, "rule": rule, "table": table, "via": via}


# (domain, rule name) -> the rule's type, text, table and via. A name the baseline's dump does not
# have is added after its rules.
RULES: dict[tuple[str, str], dict[str, Any]] = {
    ("education", "enrolled_leq_capacity"): _rule(
        "cross_column", "enrolled_count <= capacity", "course_section"
    ),
    ("financial", "account_opened_after_customer_since"): _rule(
        "cross_table", "account.opened_date >= customer.customer_since", via="customer_id"
    ),
    ("financial", "loan_after_customer_since"): _rule(
        "cross_table", "loan.origination_date >= customer.customer_since", via="customer_id"
    ),
    ("financial", "outstanding_leq_principal"): _rule(
        "cross_column", "outstanding_balance <= principal_amount", "loan"
    ),
    ("hr", "max_salary_exceeds_min"): _rule("cross_column", "max_salary > min_salary", "position"),
    ("insurance", "claim_after_policy_effective"): _rule(
        "cross_table", "claim.incident_date >= policy.effective_date", via="policy_id"
    ),
    ("insurance", "filed_after_incident"): _rule(
        "cross_column", "filed_date >= incident_date", "claim"
    ),
    ("insurance", "paid_within_claim_amount"): _rule(
        "cross_column", "claim_amount >= total_paid", "claim"
    ),
    ("iot", "max_range_exceeds_min"): _rule("cross_column", "max_range > min_range", "sensor"),
    ("manufacturing", "quantity_produced_leq_planned"): _rule(
        "cross_column", "quantity_produced <= quantity_planned", "work_order"
    ),
    ("manufacturing", "sell_price_gte_unit_cost"): _rule(
        "cross_column", "sell_price >= unit_cost", "product"
    ),
    ("supply_chain", "received_leq_ordered"): _rule(
        "cross_column", "quantity_received <= quantity_ordered", "purchase_order_line"
    ),
    ("supply_chain", "shipment_delivery_after_ship"): _rule(
        "cross_column", "delivery_date > ship_date", "shipment"
    ),
}

# Reference datasets of the baseline's domain that no column reads and the domain does not ship.
# ``peril_types`` is a coarser list of the causes ``claim_categories`` already names.
DROPPED_DATASETS: dict[str, frozenset[str]] = {"insurance": frozenset({"peril_types"})}


def corrected_columns(domain: str) -> set[tuple[str, str]]:
    """The ``(table, column)`` pairs of ``domain`` whose generator differs from the baseline's or
    that the baseline does not have."""
    return {(t, c) for d, t, c in GENERATORS if d == domain} | {
        (t, c) for d, t, c in ADDED_COLUMNS if d == domain
    }


def added_columns(domain: str) -> set[tuple[str, str]]:
    return {(t, c) for d, t, c in ADDED_COLUMNS if d == domain}


def corrected_rules(domain: str) -> set[str]:
    return {name for d, name in RULES if d == domain}


def by_table(
    domains: list[str], *, prefixed: bool
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """For the tables of ``domains`` (named ``<domain>_<table>`` in a composite, when
    ``prefixed``): the corrected columns and, among them, the added ones."""
    corrected: dict[str, set[str]] = {}
    added: dict[str, set[str]] = {}
    for domain in domains:
        new = added_columns(domain)
        for table, column in corrected_columns(domain):
            name = f"{domain}_{table}" if prefixed else table
            corrected.setdefault(name, set()).add(column)
            if (table, column) in new:
                added.setdefault(name, set()).add(column)
    return corrected, added


def apply(doc: dict[str, Any], domain: str) -> dict[str, Any]:
    """``doc`` (a schema document of ``domain``) with the corrections applied, in place."""
    for (d, table, column), (before, definition) in ADDED_COLUMNS.items():
        if d != domain:
            continue
        columns = doc["tables"][table]["columns"]
        items = [(k, v) for k, v in columns.items() if k != column]
        at = len(items) if before is None else [k for k, _ in items].index(before)
        items.insert(at, (column, copy.deepcopy(definition)))
        doc["tables"][table]["columns"] = dict(items)
    for (d, table, column), generator in GENERATORS.items():
        if d == domain:
            doc["tables"][table]["columns"][column]["generator"] = copy.deepcopy(generator)
    rules = doc["business_rules"]
    for (d, name), fields in RULES.items():
        if d != domain:
            continue
        found = next((r for r in rules if r["name"] == name), None)
        if found is None:
            found = {"name": name, "type": "", "rule": "", "table": None, "via": None, "when": None}
            rules.append(found)
        found.update(copy.deepcopy(fields))
    return doc
