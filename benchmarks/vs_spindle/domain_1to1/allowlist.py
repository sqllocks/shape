"""Where Shape deliberately differs from the baseline, on purpose (P6-01e).

The owner's standing decision (2026-10-01): a defect in the baseline that harms the user's trust is
fixed in Shape, not reproduced, and the harness names each one here, narrowly: the id, what the
baseline does, what Shape does, and exactly which tables and columns differ. ``verify.py`` applies
these entries to composite runs and nothing else; every other column, clause and tolerance is
unchanged. The product tests assert the correct behaviour
(``tests/generation/test_composite_p601e.py``).

Standard library only.
"""

from __future__ import annotations

from typing import Any

# ---- CMP-1 ---------------------------------------------------------------------------------

BRIDGE_ON_KEY: dict[str, Any] = {
    "id": "CMP-1",
    "title": "a shared-entity link must not turn a table's own key into a foreign key",
    "baseline": (
        "A preset's explicit link such as person: hr.employee -> retail customer.customer_id names "
        "a column the table already has: its own primary key. The baseline records a relationship "
        "hr_employee.employee_id -> retail_customer.customer_id and adds nothing, so half of the "
        "customers (and 77% of the financial accounts) point at employees that do not exist."
    ),
    "shape": (
        "The link gets a bridge column of its own, shared_person_hr_employee_id, a real foreign "
        "key to hr_employee.employee_id (integrity 100%). The table's own key is left alone."
    ),
    # composite id -> table -> the extra column Shape adds at the end of the table
    "extra_columns": {
        "composite_enterprise": {
            "retail_customer": "shared_person_hr_employee_id",
            "financial_account": "shared_person_hr_employee_id",
        },
        "composite_healthcare_system": {
            "healthcare_patient": "shared_person_hr_employee_id",
            "insurance_policy": "shared_person_hr_employee_id",
        },
    },
    # composite id -> (child table, the baseline's child column) of the relationships whose
    # foreign key Shape redirects to the bridge column
    "redirected": {
        "composite_enterprise": {
            "retail_customer": "customer_id",
            "financial_account": "account_id",
        },
        "composite_healthcare_system": {
            "healthcare_patient": "patient_id",
            "insurance_policy": "policy_id",
        },
    },
}

# ---- CMP-2 ---------------------------------------------------------------------------------

DATASET_CLASH: dict[str, Any] = {
    "id": "CMP-2",
    "title": "two domains' reference datasets with one name must not share a pool",
    "baseline": (
        "A reference dataset is found by its name alone and cached for the process. Education and "
        "HR each ship department_names (university departments; company departments). In a "
        "composite the first one loaded serves both: in campus the education departments are named "
        "'Supply Chain' and 'Business Development'."
    ),
    "shape": (
        "Each domain's references read that domain's own dataset when the names clash "
        "(education.department_names, hr.department_names)."
    ),
    # composite id -> every (table, column) whose dataset reference is renamed to the domain's own
    "renamed": {
        "composite_campus": [
            ("education_department", "department_name"),
            ("hr_department", "department_name"),
        ],
    },
    # composite id -> (table, column) -> (domain, dataset) the values must come from, for the
    # columns where the baseline draws from the wrong domain's dataset
    "pool_columns": {
        "composite_campus": {
            ("education_department", "department_name"): ("education", "department_names"),
        },
    },
}

ALLOWED = (BRIDGE_ON_KEY, DATASET_CLASH)


def extra_columns(domain: str) -> dict[str, str]:
    """Table -> the column Shape adds in ``domain`` (a composite id) and the baseline lacks."""
    out: dict[str, str] = BRIDGE_ON_KEY["extra_columns"].get(domain, {})
    return dict(out)


def redirected(domain: str) -> dict[str, str]:
    """Table -> the baseline's column of a cross-domain foreign key that Shape moves to a bridge."""
    out: dict[str, str] = BRIDGE_ON_KEY["redirected"].get(domain, {})
    return dict(out)


def renamed(domain: str) -> list[tuple[str, str]]:
    """The columns whose dataset reference Shape renames to the domain's own (a clash)."""
    out: list[tuple[str, str]] = DATASET_CLASH["renamed"].get(domain, [])
    return list(out)


def pool_columns(domain: str) -> dict[tuple[str, str], tuple[str, str]]:
    out: dict[tuple[str, str], tuple[str, str]] = DATASET_CLASH["pool_columns"].get(domain, {})
    return dict(out)
