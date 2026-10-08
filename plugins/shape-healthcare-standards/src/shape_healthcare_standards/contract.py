"""The input table contract of the healthcare standard outputs.

Every writer in this plugin (X12 837P/837I/835/834, FHIR R4, OMOP CDM, NCPDP mapping) reads
the **payer tables** below, by column name. The payer domain pack (lane ``HC-domain``) produces
tables with these names and columns; a table that has extra columns is fine (they are ignored),
a table that lacks a *required* column is an error, and a table that lacks an *optional* column
is treated as if that column were null.

Conventions
-----------
* Identifiers and codes are strings (``string`` or ``large_string``): NPIs, NDCs (11 digits, no
  hyphens, leading zeros kept), ZIP codes, ICD-10 codes (no decimal point, e.g. ``E1165``),
  CPT/HCPCS codes, revenue codes, type of bill.
* Dates are ``date32`` (``timestamp`` is accepted and truncated to the day).
* Money is ``float64`` (decimal columns keep ``dtype: float``, decision of 2026-10-02) and is
  written with two decimals, rounded half-to-even on its shortest decimal representation.
* Booleans are ``bool``. Counts and sequence numbers are integers.
* A row's key columns never repeat within a table: ``member_id``; ``npi``; ``claim_id``;
  ``(claim_id, line_number)``; ``(claim_id, sequence)``; ``rx_claim_id``; ``ndc``.
* All identifiers are synthetic. No writer checks them against a real registry.

Code values follow the codes lane's code sets: ICD-10-CM / ICD-10-PCS, CPT / HCPCS, NDC, NPI,
CARC / RARC, place of service and taxonomy. The writers copy codes through; they validate
only the *shape* of a code (see :mod:`shape_healthcare_standards.codes`).

Tables
------
``member``              one row per person: subscriber and dependents.
``eligibility``         coverage spans of a member in a plan.
``provider``            billing, rendering, facility and pharmacy providers (by NPI).
``medical_claim``       claim header, professional (``claim_type`` ``P``) or institutional (``I``).
``medical_claim_line``  service lines of a claim.
``claim_acknowledgment``  acknowledgment of a claim or service line (277CA); key
                        ``(claim_id, line_number)``, a null ``line_number`` being the claim level.
``claim_diagnosis``     ordered ICD-10-CM codes of a claim.
``claim_procedure``     ICD-10-PCS codes of an inpatient claim.
``pharmacy_claim``      one row per pharmacy claim transaction.
``drug_reference``      drug attributes by NDC.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

_S = pa.string()
_D = pa.date32()
_F = pa.float64()
_I = pa.int64()
_B = pa.bool_()


class ContractError(ValueError):
    """An input table does not meet the contract."""


@dataclass(frozen=True, slots=True)
class Col:
    """One contract column: name, Arrow type, whether the column must exist, and its meaning."""

    name: str
    type: pa.DataType
    required: bool
    doc: str


def _c(name: str, typ: pa.DataType, required: bool, doc: str) -> Col:
    return Col(name, typ, required, doc)


CONTRACT: dict[str, tuple[Col, ...]] = {
    "member": (
        _c("member_id", _S, True, "unique member id (subscriber id + member suffix)"),
        _c("subscriber_id", _S, True, "subscriber id; equals the member's own for the subscriber"),
        _c(
            "relationship_code",
            _S,
            True,
            "X12 individual relationship: 18 self, 01 spouse, 19 child",
        ),
        _c("first_name", _S, True, "given name"),
        _c("last_name", _S, True, "family name"),
        _c("birth_date", _D, True, "date of birth"),
        _c("sex", _S, True, "M, F or U"),
        _c("member_suffix", _S, False, "two-digit suffix within the subscriber's family"),
        _c("middle_name", _S, False, "middle name or initial"),
        _c("address_line1", _S, False, "street address"),
        _c("address_line2", _S, False, "unit"),
        _c("city", _S, False, "city"),
        _c("state", _S, False, "two-letter state"),
        _c("zip", _S, False, "ZIP code, 5 or 9 digits, leading zeros kept"),
        _c("phone", _S, False, "10-digit telephone number"),
        _c("race", _S, False, "OMB race category name"),
        _c("ethnicity", _S, False, "Hispanic or Latino, Not Hispanic or Latino"),
        _c("language", _S, False, "ISO 639 language code"),
        _c("marital_status", _S, False, "X12 marital status: I single, M married, ..."),
        _c("deceased_date", _D, False, "date of death"),
    ),
    "eligibility": (
        _c("member_id", _S, True, "member the span belongs to"),
        _c("plan_id", _S, True, "plan or product id"),
        _c("coverage_start", _D, True, "first covered day"),
        _c("coverage_end", _D, True, "last covered day; null for an open span"),
        _c("eligibility_id", _S, False, "unique id of the span"),
        _c("group_id", _S, False, "employer or group id"),
        _c("group_name", _S, False, "group name"),
        _c("plan_name", _S, False, "plan name"),
        _c("plan_type", _S, False, "COMMERCIAL, MEDICARE_ADVANTAGE, MEDICAID or ACA"),
        _c("coverage_level", _S, False, "X12 coverage level: IND, FAM, E1D, ESP, ECH"),
        _c("insurance_line", _S, False, "X12 line of business: HLT, DEN, VIS"),
        _c("payer_id", _S, False, "synthetic payer id"),
        _c("payer_name", _S, False, "payer name"),
        _c("pcp_npi", _S, False, "attributed primary care provider"),
        _c("cob_flag", _B, False, "other coverage exists (coordination of benefits)"),
        _c("termination_reason", _S, False, "reason when the span ended"),
    ),
    "provider": (
        _c("npi", _S, True, "synthetic NPI (10 digits, Luhn check digit)"),
        _c("entity_type", _S, True, "1 individual, 2 organisation"),
        _c("last_name", _S, False, "individual: family name"),
        _c("first_name", _S, False, "individual: given name"),
        _c("org_name", _S, False, "organisation name"),
        _c("taxonomy_code", _S, False, "NUCC taxonomy code"),
        _c("specialty", _S, False, "specialty name"),
        _c("tax_id", _S, False, "synthetic 9-digit tax id (EIN)"),
        _c("address_line1", _S, False, "street address"),
        _c("city", _S, False, "city"),
        _c("state", _S, False, "two-letter state"),
        _c("zip", _S, False, "ZIP code"),
        _c("network_status", _S, False, "IN or OUT of network"),
        _c("ncpdp_id", _S, False, "synthetic 7-digit pharmacy id"),
    ),
    "medical_claim": (
        _c("claim_id", _S, True, "unique claim id (patient control number)"),
        _c("claim_type", _S, True, "P professional (837P), I institutional (837I)"),
        _c("member_id", _S, True, "patient"),
        _c("billing_npi", _S, True, "billing provider"),
        _c("service_from_date", _D, True, "first date of service"),
        _c("service_to_date", _D, True, "last date of service"),
        _c("total_billed", _F, True, "total charge"),
        _c("claim_frequency_code", _S, False, "1 original, 7 replacement, 8 void (default 1)"),
        _c("rendering_npi", _S, False, "rendering provider (professional)"),
        _c("facility_npi", _S, False, "service facility"),
        _c("attending_npi", _S, False, "attending provider (institutional)"),
        _c("place_of_service", _S, False, "professional: CMS place-of-service code"),
        _c("type_of_bill", _S, False, "institutional: 3-digit type of bill (e.g. 111)"),
        _c("admission_date", _D, False, "inpatient admission"),
        _c("discharge_date", _D, False, "inpatient discharge"),
        _c("admission_type_code", _S, False, "institutional: 1 emergency, 2 urgent, 3 elective"),
        _c("patient_status_code", _S, False, "institutional discharge status, e.g. 01"),
        _c("drg_code", _S, False, "MS-DRG, 3 digits"),
        _c("received_date", _D, False, "date the payer received the claim"),
        _c("adjudication_date", _D, False, "date of adjudication or payment"),
        _c("claim_status", _S, False, "paid, denied, adjusted, reversed, pending"),
        _c("total_allowed", _F, False, "allowed amount"),
        _c("total_paid", _F, False, "plan paid amount"),
        _c("member_copay", _F, False, "copay"),
        _c("member_coinsurance", _F, False, "coinsurance"),
        _c("member_deductible", _F, False, "deductible"),
        _c("prior_auth_number", _S, False, "prior authorization number"),
        _c("original_claim_id", _S, False, "claim this one replaces or voids"),
        _c("payer_claim_number", _S, False, "payer claim control number (default claim_id)"),
        _c("plan_id", _S, False, "plan that adjudicated"),
        _c("payer_id", _S, False, "synthetic payer id"),
        _c("payer_name", _S, False, "payer name"),
    ),
    "medical_claim_line": (
        _c("claim_id", _S, True, "claim"),
        _c("line_number", _I, True, "1-based line number"),
        _c(
            "procedure_code",
            _S,
            False,
            "CPT or HCPCS code (professional; optional on institutional)",
        ),
        _c("service_date", _D, True, "date of service"),
        _c("billed_amount", _F, True, "line charge"),
        _c("units", _F, False, "units (default 1)"),
        _c("modifier_1", _S, False, "procedure modifier"),
        _c("modifier_2", _S, False, "procedure modifier"),
        _c("modifier_3", _S, False, "procedure modifier"),
        _c("modifier_4", _S, False, "procedure modifier"),
        _c("revenue_code", _S, False, "institutional revenue code, 4 digits"),
        _c("place_of_service", _S, False, "line place of service"),
        _c("diagnosis_pointers", _S, False, "comma list of claim_diagnosis sequences, e.g. 1,2"),
        _c("allowed_amount", _F, False, "line allowed"),
        _c("paid_amount", _F, False, "line paid"),
        _c("copay", _F, False, "line copay"),
        _c("coinsurance", _F, False, "line coinsurance"),
        _c("deductible", _F, False, "line deductible"),
        _c("line_status", _S, False, "paid, denied, adjusted, reversed"),
        _c("denial_carc", _S, False, "CARC code when the line was reduced or denied"),
        _c("denial_rarc", _S, False, "RARC code"),
    ),
    "claim_acknowledgment": (
        _c("claim_id", _S, True, "acknowledged claim; matches medical_claim.claim_id"),
        _c("acknowledgment_date", _D, True, "date of the acknowledgment (STC02, DTP*009)"),
        _c("status_category_code", _S, True, "claim status category, e.g. A1, A2, A3, A6, A7"),
        _c("status_code", _S, True, "claim status code, 1 to 5 digits"),
        _c(
            "line_number",
            _I,
            False,
            "service line (matches medical_claim_line); null for the claim level",
        ),
        _c("entity_identifier_code", _S, False, "entity the status refers to, 2 or 3 characters"),
        _c("action_code", _S, False, "WQ accepted or U rejected; derived from the category"),
        _c("reference_number", _S, False, "acknowledging party's claim control number"),
        _c("received_date", _D, False, "date the acknowledging party received the claim"),
    ),
    "claim_diagnosis": (
        _c("claim_id", _S, True, "claim"),
        _c("sequence", _I, True, "1-based order; 1 is the principal diagnosis"),
        _c("icd10_code", _S, True, "ICD-10-CM code without the decimal point"),
        _c("diagnosis_type", _S, False, "principal, admitting, secondary or external"),
        _c("poa_indicator", _S, False, "present on admission: Y, N, U, W or blank"),
    ),
    "claim_procedure": (
        _c("claim_id", _S, True, "claim"),
        _c("sequence", _I, True, "1-based order"),
        _c("icd10pcs_code", _S, True, "ICD-10-PCS code, 7 characters"),
        _c("procedure_date", _D, False, "date of the procedure"),
    ),
    "pharmacy_claim": (
        _c("rx_claim_id", _S, True, "unique pharmacy claim id"),
        _c("member_id", _S, True, "patient"),
        _c("ndc", _S, True, "11-digit NDC, no hyphens"),
        _c("fill_date", _D, True, "date of service (fill)"),
        _c("quantity", _F, True, "quantity dispensed"),
        _c("days_supply", _I, True, "days supply"),
        _c("pharmacy_npi", _S, False, "dispensing pharmacy NPI"),
        _c("prescriber_npi", _S, False, "prescriber NPI"),
        _c("written_date", _D, False, "date the prescription was written"),
        _c("rx_number", _S, False, "prescription number"),
        _c("refill_number", _I, False, "0 for the original fill"),
        _c("daw_code", _S, False, "dispense as written code, 0-9"),
        _c("drug_name", _S, False, "drug name as dispensed"),
        _c("pharmacy_ncpdp_id", _S, False, "synthetic 7-digit pharmacy id"),
        _c("ingredient_cost", _F, False, "ingredient cost"),
        _c("dispensing_fee", _F, False, "dispensing fee"),
        _c("patient_pay", _F, False, "patient pay amount"),
        _c("plan_paid", _F, False, "plan paid amount"),
        _c("claim_status", _S, False, "paid, rejected or reversed"),
        _c("reject_code", _S, False, "reject code when rejected"),
        _c("mail_order", _B, False, "mail order versus retail"),
        _c("formulary_tier", _I, False, "formulary tier"),
        _c("plan_id", _S, False, "plan"),
        _c("bin_number", _S, False, "synthetic 6-digit BIN"),
        _c("pcn", _S, False, "processor control number"),
        _c("group_id", _S, False, "group"),
    ),
    "drug_reference": (
        _c("ndc", _S, True, "11-digit NDC"),
        _c("drug_name", _S, True, "proprietary or labelled name"),
        _c("generic_name", _S, False, "nonproprietary name"),
        _c("strength", _S, False, "strength text"),
        _c("dose_form", _S, False, "dose form"),
        _c("route", _S, False, "route"),
        _c("rxnorm_code", _S, False, "RxNorm concept id"),
        _c("therapeutic_class", _S, False, "therapeutic class"),
        _c("brand_generic", _S, False, "brand or generic"),
        _c("dea_schedule", _S, False, "controlled substance schedule, e.g. CII"),
        _c("labeler", _S, False, "labeler name"),
    ),
}

TABLE_NAMES: tuple[str, ...] = tuple(CONTRACT)

# Per-table key columns, for the duplicate-key check.
KEYS: dict[str, tuple[str, ...]] = {
    "member": ("member_id",),
    "provider": ("npi",),
    "medical_claim": ("claim_id",),
    "medical_claim_line": ("claim_id", "line_number"),
    "claim_acknowledgment": ("claim_id", "line_number"),
    "claim_diagnosis": ("claim_id", "sequence"),
    "claim_procedure": ("claim_id", "sequence"),
    "pharmacy_claim": ("rx_claim_id",),
    "drug_reference": ("ndc",),
}


# Key columns that may be null: a null ``line_number`` is the claim level.
NULLABLE_KEYS: dict[str, tuple[str, ...]] = {"claim_acknowledgment": ("line_number",)}


def contract_schema(table: str) -> pa.Schema:
    """The Arrow schema of one contract table (all columns, required and optional)."""
    return pa.schema([pa.field(c.name, c.type) for c in _cols(table)])


def _cols(table: str) -> tuple[Col, ...]:
    try:
        return CONTRACT[table]
    except KeyError:
        raise ContractError(f"unknown table {table!r}; contract tables: {list(CONTRACT)}") from None


def _is_text(t: pa.DataType) -> bool:
    return bool(pa.types.is_string(t) or pa.types.is_large_string(t))


def coerce(table: str, data: pa.Table | pa.RecordBatch) -> pa.Table:
    """Return ``data`` as exactly the contract's columns and types.

    Extra columns are dropped; a missing optional column becomes null; a missing required
    column raises :class:`ContractError`. Text columns accept any string type, dates accept
    timestamps (truncated to the day), money accepts any numeric type (including decimals).
    """
    tbl = pa.Table.from_batches([data]) if isinstance(data, pa.RecordBatch) else data
    cols = _cols(table)
    missing = [c.name for c in cols if c.required and c.name not in tbl.column_names]
    if missing:
        raise ContractError(f"table {table!r} lacks required column(s) {missing}")
    arrays: list[pa.ChunkedArray | pa.Array] = []
    for c in cols:
        if c.name not in tbl.column_names:
            arrays.append(pa.nulls(tbl.num_rows, c.type))
            continue
        col = tbl.column(c.name)
        arrays.append(_cast(col, c, table))
    return pa.Table.from_arrays(arrays, schema=contract_schema(table))


def _cast(col: pa.ChunkedArray, c: Col, table: str) -> pa.ChunkedArray | pa.Array:
    src = col.type
    if src == c.type:
        return col
    try:
        if pa.types.is_date32(c.type) and pa.types.is_timestamp(src):
            return pc.cast(col, c.type)
        if pa.types.is_floating(c.type) and (
            pa.types.is_integer(src) or pa.types.is_decimal(src) or pa.types.is_floating(src)
        ):
            return pc.cast(col, c.type)
        if pa.types.is_integer(c.type) and (pa.types.is_integer(src) or pa.types.is_floating(src)):
            return pc.cast(col, c.type)
        if _is_text(c.type) and _is_text(src):
            return pc.cast(col, c.type)
        if pa.types.is_null(src):
            return pa.nulls(len(col), c.type)
    except pa.ArrowInvalid as exc:
        raise ContractError(f"{table}.{c.name}: cannot convert {src} to {c.type}: {exc}") from None
    raise ContractError(f"{table}.{c.name}: type {src} does not match the contract type {c.type}")


def coerce_all(tables: Mapping[str, pa.Table | pa.RecordBatch]) -> dict[str, pa.Table]:
    """Coerce every table present; names outside the contract are rejected."""
    return {name: coerce(name, t) for name, t in tables.items()}


def check_keys(table: str, data: pa.Table) -> list[str]:
    """Problems with the table's key columns: nulls and duplicates. Empty list when fine."""
    keys = KEYS.get(table)
    if keys is None:
        return []
    problems: list[str] = []
    for k in keys:
        if k in NULLABLE_KEYS.get(table, ()):
            continue
        if data.column(k).null_count:
            problems.append(f"{table}.{k}: null key value(s)")
    if not problems and data.num_rows:
        grouped = data.group_by(list(keys)).aggregate([([], "count_all")])
        if grouped.num_rows != data.num_rows:
            problems.append(f"{table}: duplicate key {list(keys)}")
    return problems
