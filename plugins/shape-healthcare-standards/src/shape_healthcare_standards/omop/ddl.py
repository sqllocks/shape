"""OMOP CDM v5.4 table definitions as Arrow schemas, for the tables the writer populates.

Column names, order, nullability and types follow the official OHDSI v5.4 DuckDB DDL
(``integer`` is ``int32``, ``NUMERIC`` is ``decimal(18, 3)``, ``TIMESTAMP`` is ``timestamp[us]``,
``varchar(n)`` is ``string`` with the limit kept in :data:`VARCHAR_LIMITS`).
"""

from __future__ import annotations

import pyarrow as pa  # type: ignore[import-untyped]

_INT = pa.int32()
_DATE = pa.date32()
_TS = pa.timestamp("us")
_NUM = pa.decimal128(18, 3)

VARCHAR_LIMITS: dict[str, dict[str, int]] = {}


def _schema(table: str, spec: str) -> pa.Schema:
    """Build a schema from ``name:kind`` items.

    ``kind`` is ``i`` integer, ``d`` date, ``t`` timestamp, ``n`` numeric, ``x`` text or
    ``s<limit>`` varchar; a trailing ``!`` marks NOT NULL.
    """
    fields: list[pa.Field] = []
    limits: dict[str, int] = {}
    for item in spec.split():
        name, kind = item.split(":")
        nullable = not kind.endswith("!")
        kind = kind.rstrip("!")
        if kind == "i":
            typ = _INT
        elif kind == "d":
            typ = _DATE
        elif kind == "t":
            typ = _TS
        elif kind == "n":
            typ = _NUM
        elif kind == "x":
            typ = pa.string()
        else:
            typ = pa.string()
            limits[name] = int(kind[1:])
        fields.append(pa.field(name, typ, nullable=nullable))
    if limits:
        VARCHAR_LIMITS[table] = limits
    return pa.schema(fields)


_SPECS: dict[str, str] = {
    "person": (
        "person_id:i! gender_concept_id:i! year_of_birth:i! month_of_birth:i "
        "day_of_birth:i birth_datetime:t race_concept_id:i! ethnicity_concept_id:i! "
        "location_id:i provider_id:i care_site_id:i person_source_value:s50 "
        "gender_source_value:s50 gender_source_concept_id:i race_source_value:s50 "
        "race_source_concept_id:i ethnicity_source_value:s50 "
        "ethnicity_source_concept_id:i"
    ),
    "observation_period": (
        "observation_period_id:i! person_id:i! observation_period_start_date:d! "
        "observation_period_end_date:d! period_type_concept_id:i!"
    ),
    "payer_plan_period": (
        "payer_plan_period_id:i! person_id:i! payer_plan_period_start_date:d! "
        "payer_plan_period_end_date:d! payer_concept_id:i payer_source_value:s50 "
        "payer_source_concept_id:i plan_concept_id:i plan_source_value:s50 "
        "plan_source_concept_id:i sponsor_concept_id:i sponsor_source_value:s50 "
        "sponsor_source_concept_id:i family_source_value:s50 stop_reason_concept_id:i "
        "stop_reason_source_value:s50 stop_reason_source_concept_id:i"
    ),
    "visit_occurrence": (
        "visit_occurrence_id:i! person_id:i! visit_concept_id:i! visit_start_date:d! "
        "visit_start_datetime:t visit_end_date:d! visit_end_datetime:t "
        "visit_type_concept_id:i! provider_id:i care_site_id:i visit_source_value:s50 "
        "visit_source_concept_id:i admitted_from_concept_id:i "
        "admitted_from_source_value:s50 discharged_to_concept_id:i "
        "discharged_to_source_value:s50 preceding_visit_occurrence_id:i"
    ),
    "condition_occurrence": (
        "condition_occurrence_id:i! person_id:i! condition_concept_id:i! "
        "condition_start_date:d! condition_start_datetime:t condition_end_date:d "
        "condition_end_datetime:t condition_type_concept_id:i! "
        "condition_status_concept_id:i stop_reason:s20 provider_id:i "
        "visit_occurrence_id:i visit_detail_id:i condition_source_value:s50 "
        "condition_source_concept_id:i condition_status_source_value:s50"
    ),
    "procedure_occurrence": (
        "procedure_occurrence_id:i! person_id:i! procedure_concept_id:i! "
        "procedure_date:d! procedure_datetime:t procedure_end_date:d "
        "procedure_end_datetime:t procedure_type_concept_id:i! modifier_concept_id:i "
        "quantity:i provider_id:i visit_occurrence_id:i visit_detail_id:i "
        "procedure_source_value:s50 procedure_source_concept_id:i "
        "modifier_source_value:s50"
    ),
    "drug_exposure": (
        "drug_exposure_id:i! person_id:i! drug_concept_id:i! "
        "drug_exposure_start_date:d! drug_exposure_start_datetime:t "
        "drug_exposure_end_date:d! drug_exposure_end_datetime:t verbatim_end_date:d "
        "drug_type_concept_id:i! stop_reason:s20 refills:i quantity:n days_supply:i "
        "sig:x route_concept_id:i lot_number:s50 provider_id:i visit_occurrence_id:i "
        "visit_detail_id:i drug_source_value:s50 drug_source_concept_id:i "
        "route_source_value:s50 dose_unit_source_value:s50"
    ),
    "cost": (
        "cost_id:i! cost_event_id:i! cost_domain_id:s20! cost_type_concept_id:i! "
        "currency_concept_id:i total_charge:n total_cost:n total_paid:n "
        "paid_by_payer:n paid_by_patient:n paid_patient_copay:n "
        "paid_patient_coinsurance:n paid_patient_deductible:n paid_by_primary:n "
        "paid_ingredient_cost:n paid_dispensing_fee:n payer_plan_period_id:i "
        "amount_allowed:n revenue_code_concept_id:i revenue_code_source_value:s50 "
        "drg_concept_id:i drg_source_value:s3"
    ),
    "provider": (
        "provider_id:i! provider_name:s255 npi:s20 dea:s20 specialty_concept_id:i "
        "care_site_id:i year_of_birth:i gender_concept_id:i provider_source_value:s50 "
        "specialty_source_value:s50 specialty_source_concept_id:i "
        "gender_source_value:s50 gender_source_concept_id:i"
    ),
    "care_site": (
        "care_site_id:i! care_site_name:s255 place_of_service_concept_id:i "
        "location_id:i care_site_source_value:s50 place_of_service_source_value:s50"
    ),
    "location": (
        "location_id:i! address_1:s50 address_2:s50 city:s50 state:s2 zip:s9 "
        "county:s20 location_source_value:s50 country_concept_id:i "
        "country_source_value:s80 latitude:n longitude:n"
    ),
    "death": (
        "person_id:i! death_date:d! death_datetime:t death_type_concept_id:i "
        "cause_concept_id:i cause_source_value:s50 cause_source_concept_id:i"
    ),
}

SCHEMAS: dict[str, pa.Schema] = {name: _schema(name, spec) for name, spec in _SPECS.items()}

TABLE_NAMES: tuple[str, ...] = tuple(SCHEMAS)
