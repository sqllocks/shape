"""The healthcare domain: providers, facilities, patients, encounters, diagnoses, procedures,
medications, claims and claim lines (9 tables), with the reference data it draws from (CPT and
ICD-10 codes, insurance plans, medication names and specialties; the ZIP locations are the retail
domain's)."""

from __future__ import annotations

from shape_domains._packaged import PackagedDomain

SHAPE_API = "1.0"


class HealthcareDomain(PackagedDomain):
    """``shape.domains`` entry ``healthcare``."""

    name = "healthcare"
    description = "Healthcare domain with patients, encounters, diagnoses, procedures, and claims"
    datasets = ("cpt_codes", "icd10_codes", "insurance_plans", "medication_names", "specialties")
    borrowed = {"us_zip_locations": ("retail", "us_zip_locations")}
