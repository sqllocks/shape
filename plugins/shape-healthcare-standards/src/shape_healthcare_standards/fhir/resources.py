"""Payer contract rows to FHIR R4 (4.0.1) resources, as plain ``dict``s.

Every function is pure: the same rows give the same resource. Resource ids are derived from the
source key (:func:`make_id`), so a reference can be written without the target being present.
A reference is a typed ``Type/id`` reference only when the target's source row is known (the
provider table, the eligibility table, the claim table); otherwise it is a logical reference
that carries an identifier, never a dangling ``Type/id`` for a resource nobody emitted.

Systems that FHIR does not define (member ids, claim ids, ...) are URNs under
:data:`SYSTEM_BASE`: every value here is synthetic.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from collections.abc import Iterator, Mapping
from typing import Any

from ..common import TableSet, iso, money
from ..contract import ContractError

Resource = dict[str, Any]

SYSTEM_BASE = "urn:shape:healthcare"
SYS_MEMBER = f"{SYSTEM_BASE}:member-id"
SYS_SUBSCRIBER = f"{SYSTEM_BASE}:subscriber-id"
SYS_ELIGIBILITY = f"{SYSTEM_BASE}:eligibility-id"
SYS_CLAIM = f"{SYSTEM_BASE}:claim-id"
SYS_PAYER_CLAIM = f"{SYSTEM_BASE}:payer-claim-number"
SYS_PAYER = f"{SYSTEM_BASE}:payer-id"
SYS_RX_CLAIM = f"{SYSTEM_BASE}:rx-claim-id"
SYS_RX_NUMBER = f"{SYSTEM_BASE}:rx-number"
SYS_NCPDP_ID = f"{SYSTEM_BASE}:ncpdp-provider-id"
SYS_NCPDP_REJECT = f"{SYSTEM_BASE}:ncpdp-reject-code"

SYS_NPI = "http://hl7.org/fhir/sid/us-npi"
SYS_EIN = "urn:oid:2.16.840.1.113883.4.4"
SYS_NDC = "http://hl7.org/fhir/sid/ndc"
SYS_RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"
SYS_ICD10CM = "http://hl7.org/fhir/sid/icd-10-cm"
SYS_ICD10PCS = "http://www.cms.gov/Medicare/Coding/ICD10"
SYS_CPT = "http://www.ama-assn.org/go/cpt"
SYS_HCPCS = "https://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets"
SYS_REVENUE = "https://www.nubc.org/CodeSystem/RevenueCodes"
SYS_POS = "https://www.cms.gov/Medicare/Coding/place-of-service-codes/Place_of_Service_Code_Set"
SYS_CARC = "https://x12.org/codes/claim-adjustment-reason-codes"
SYS_RARC = "https://x12.org/codes/remittance-advice-remark-codes"
SYS_TAXONOMY = "http://nucc.org/provider-taxonomy"
SYS_TYPE_OF_BILL = "https://www.nubc.org/CodeSystem/TypeOfBill"
SYS_ADMIT_TYPE = "https://www.nubc.org/CodeSystem/PriorityTypeOfAdmitOrVisit"
SYS_DISCHARGE = "https://www.nubc.org/CodeSystem/PatientDischargeStatus"
SYS_MSDRG = (
    "http://www.cms.gov/Medicare/Medicare-Fee-for-Service-Payment/AcuteInpatientPPS/"
    "MS-DRG-Classification-and-Software"
)

_TERM = "http://terminology.hl7.org/CodeSystem"
_CARIN = "http://hl7.org/fhir/us/carin-bb/CodeSystem"
SYS_CLAIM_TYPE = f"{_TERM}/claim-type"
SYS_ADJUDICATION = f"{_TERM}/adjudication"
SYS_CARIN_ADJUDICATION = f"{_CARIN}/C4BBAdjudication"
SYS_CARIN_SUPPORTING_INFO = f"{_CARIN}/C4BBSupportingInfoType"
SYS_CARIN_CARE_TEAM_ROLE = f"{_CARIN}/C4BBClaimCareTeamRole"
SYS_POA = "https://www.cms.gov/Medicare/Medicare-Fee-for-Service-Payment/HospitalAcqCond/Coding"

US_CORE = "http://hl7.org/fhir/us/core/StructureDefinition"
OMB_RACE_ETHNICITY = "urn:oid:2.16.840.1.113883.6.238"

_ID_OK = re.compile(r"^[A-Za-z0-9\-.]{1,64}$")
_BAD_ID_CHARS = re.compile(r"[^A-Za-z0-9\-.]+")


def make_id(key: str, prefix: str = "") -> str:
    """A FHIR-valid, deterministic id for a source key.

    A key that is already valid is kept; otherwise the id is a readable slug plus a hash of the
    whole key, so different keys never share an id.
    """
    text = f"{prefix}{key}"
    if _ID_OK.match(text):
        return text
    slug = _BAD_ID_CHARS.sub("-", text).strip("-")[:40] or "id"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return f"{slug}-{digest}"


def payer_org_id(payer_id: str) -> str:
    return make_id(payer_id, "payer-")


def icd10cm_code(code: str) -> str:
    """FHIR writes ICD-10-CM with the point after the third character."""
    return f"{code[:3]}.{code[3:]}" if len(code) > 3 else code


def _amount(value: float | None) -> dict[str, Any]:
    return {"value": float(money(value)), "currency": "USD"}


def _qty(value: float) -> int | float:
    return int(value) if float(value).is_integer() else float(value)


def _coding(system: str, code: str, display: str | None = None) -> dict[str, Any]:
    c: dict[str, Any] = {"system": system, "code": code}
    if display:
        c["display"] = display
    return c


def _cc(system: str, code: str, display: str | None = None) -> dict[str, Any]:
    return {"coding": [_coding(system, code, display)]}


def _ident(system: str, value: str) -> dict[str, str]:
    return {"system": system, "value": value}


def _clean(value: Any) -> Any:
    """Drop ``None`` values and empty lists/dicts so a resource holds only what is known."""
    if isinstance(value, dict):
        out = {k: _clean(v) for k, v in value.items()}
        return {k: v for k, v in out.items() if v is not None and v != [] and v != {}}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


class Context:
    """The tables a mapping can look rows up in (members, providers, eligibility, claims, ...)."""

    def __init__(self, tables: TableSet) -> None:
        self.tables = tables
        self.members = tables.index("member", "member_id")
        self.providers = tables.index("provider", "npi")
        self.claims = tables.index("medical_claim", "claim_id")
        self.drugs = tables.index("drug_reference", "ndc")
        self.spans_by_member = tables.by("eligibility", "member_id")
        self.lines = tables.by("medical_claim_line", "claim_id")
        self.diagnoses = tables.by("claim_diagnosis", "claim_id")
        self.procedures = tables.by("claim_procedure", "claim_id")

    def provider_ref(self, npi: str | None) -> dict[str, Any] | None:
        """``Practitioner/..`` or ``Organization/..`` when the provider row is known, else an
        identifier-only reference."""
        if not npi:
            return None
        row = self.providers.get(npi)
        if row is None:
            return {"identifier": _ident(SYS_NPI, npi)}
        if row["entity_type"] == "1":
            return {"reference": f"Practitioner/{make_id(npi)}", "display": _person_name(row)}
        return {"reference": f"Organization/{make_id(npi)}", "display": row["org_name"]}

    def facility_ref(self, npi: str | None) -> dict[str, Any] | None:
        """Claim.facility is a Location reference; the facility is an organisation by NPI, so
        the reference carries the identifier and its display name only."""
        if not npi:
            return None
        row = self.providers.get(npi)
        return {
            "type": "Location",
            "identifier": _ident(SYS_NPI, npi),
            "display": row["org_name"] if row else None,
        }

    def coverage_ref(self, member_id: str, on: dt.date | None, plan_id: str | None) -> Resource:
        """The coverage span that held on ``on`` (preferring the claim's plan), else a logical
        reference by member id."""
        spans = [
            s
            for s in self.spans_by_member.get(member_id, [])
            if on is None
            or (
                s["coverage_start"] <= on and (s["coverage_end"] is None or on <= s["coverage_end"])
            )
        ]
        spans.sort(key=lambda s: (s["plan_id"] != plan_id, s["coverage_start"]))
        if spans:
            return {"reference": f"Coverage/{coverage_id(spans[0])}"}
        return {"identifier": _ident(SYS_MEMBER, member_id)}


def _person_name(row: Mapping[str, Any]) -> str | None:
    parts = [row.get("first_name"), row.get("last_name")]
    text = " ".join(p for p in parts if p)
    return text or None


def _payer_ref(payer_id: str | None, payer_name: str | None) -> dict[str, Any]:
    if payer_id:
        return {"reference": f"Organization/{payer_org_id(payer_id)}", "display": payer_name}
    return {"display": payer_name or "Unknown payer"}


def patient_id(member_id: str) -> str:
    return make_id(member_id)


def patient_ref(member_id: str) -> dict[str, str]:
    return {"reference": f"Patient/{patient_id(member_id)}"}


def coverage_id(row: Mapping[str, Any]) -> str:
    if row["eligibility_id"]:
        return make_id(row["eligibility_id"])
    key = f"{row['member_id']}|{row['plan_id']}|{iso(row['coverage_start'])}"
    return make_id(key, "cov-")


# --- Patient -----------------------------------------------------------------------------------

_RACE = {
    "american indian or alaska native": ("1002-5", "American Indian or Alaska Native"),
    "asian": ("2028-9", "Asian"),
    "black or african american": ("2054-5", "Black or African American"),
    "native hawaiian or other pacific islander": (
        "2076-8",
        "Native Hawaiian or Other Pacific Islander",
    ),
    "white": ("2106-3", "White"),
}
_ETHNICITY = {
    "hispanic or latino": ("2135-2", "Hispanic or Latino"),
    "not hispanic or latino": ("2186-5", "Not Hispanic or Latino"),
}
_GENDER = {"M": "male", "F": "female"}
_MARITAL = {"I": ("S", "Never Married"), "M": ("M", "Married"), "D": ("D", "Divorced"),
            "W": ("W", "Widowed"), "S": ("L", "Legally Separated")}  # fmt: skip
_LANGUAGE = {"eng": "en", "spa": "es", "fra": "fr", "fre": "fr", "deu": "de", "ger": "de",
             "zho": "zh", "chi": "zh", "vie": "vi", "rus": "ru", "ara": "ar", "kor": "ko",
             "por": "pt", "ita": "it", "jpn": "ja", "hin": "hi"}  # fmt: skip


def _omb_extension(url: str, table: dict[str, tuple[str, str]], value: str | None) -> Any:
    hit = table.get((value or "").strip().lower())
    if hit is None:
        return None
    code, display = hit
    return {
        "url": url,
        "extension": [
            {"url": "ombCategory", "valueCoding": _coding(OMB_RACE_ETHNICITY, code, display)},
            {"url": "text", "valueString": display},
        ],
    }


def _phone(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}" if len(digits) == 10 else value


def _zip(value: str | None) -> str | None:
    if value and len(value) == 9 and value.isdigit():
        return f"{value[:5]}-{value[5:]}"
    return value


def _address(row: Mapping[str, Any], use: str | None = None) -> list[dict[str, Any]]:
    lines = [x for x in (row.get("address_line1"), row.get("address_line2")) if x]
    if not (lines or row.get("city") or row.get("state") or row.get("zip")):
        return []
    return [
        {
            "use": use,
            "line": lines,
            "city": row.get("city"),
            "state": row.get("state"),
            "postalCode": _zip(row.get("zip")),
            "country": "US",
        }
    ]


def patient(row: Mapping[str, Any]) -> Resource:
    """A member row as a US Core-shaped Patient."""
    lang = row["language"]
    marital = _MARITAL.get(row["marital_status"] or "")
    given = [g for g in (row["first_name"], row["middle_name"]) if g]
    res: Resource = {
        "resourceType": "Patient",
        "id": patient_id(row["member_id"]),
        "meta": {"profile": [f"{US_CORE}/us-core-patient"]},
        "extension": [
            e
            for e in (
                _omb_extension(f"{US_CORE}/us-core-race", _RACE, row["race"]),
                _omb_extension(f"{US_CORE}/us-core-ethnicity", _ETHNICITY, row["ethnicity"]),
            )
            if e
        ],
        "identifier": [
            {
                "use": "usual",
                **_ident(SYS_MEMBER, row["member_id"]),
            }
        ],
        "active": True,
        "name": [{"use": "official", "family": row["last_name"], "given": given}],
        "telecom": [{"system": "phone", "value": _phone(row["phone"]), "use": "home"}]
        if row["phone"]
        else [],
        "gender": _GENDER.get(row["sex"] or "", "unknown"),
        "birthDate": iso(row["birth_date"]),
        "address": _address(row, "home"),
        "maritalStatus": _cc(f"{_TERM}/v3-MaritalStatus", *marital) if marital else None,
        "communication": [{"language": _cc("urn:ietf:bcp:47", _LANGUAGE.get(lang, lang))}]
        if lang
        else [],
    }
    if row["deceased_date"]:
        res["deceasedDateTime"] = iso(row["deceased_date"])
    return _clean(res)  # type: ignore[no-any-return]


# --- Coverage ----------------------------------------------------------------------------------

_RELATIONSHIP = {"18": "self", "01": "spouse", "19": "child"}
_COVERAGE_TYPE = {
    "COMMERCIAL": ("EHCPOL", "extended healthcare"),
    "ACA": ("EHCPOL", "extended healthcare"),
    "MEDICARE_ADVANTAGE": ("PUBLICPOL", "public healthcare"),
    "MEDICAID": ("PUBLICPOL", "public healthcare"),
}


def coverage(row: Mapping[str, Any], ctx: Context, as_of: dt.date | None = None) -> Resource:
    """An eligibility span as a Coverage. ``cancelled`` once the span has ended (before
    ``as_of`` when given, otherwise whenever it has an end date), else ``active``."""
    end = row["coverage_end"]
    ended = end is not None and (as_of is None or end < as_of)
    member_id = row["member_id"]
    member = ctx.members.get(member_id)
    rel_code = member["relationship_code"] if member else None
    subscriber_id = member["subscriber_id"] if member else None
    if rel_code == "18":
        subscriber: Resource | None = patient_ref(member_id)
    else:
        sub = next(
            (
                m
                for m in ctx.members.values()
                if m["subscriber_id"] == subscriber_id and m["relationship_code"] == "18"
            ),
            None,
        )
        subscriber = patient_ref(sub["member_id"]) if sub else None
    ptype = _COVERAGE_TYPE.get(row["plan_type"] or "")
    classes = []
    if row["group_id"]:
        classes.append(
            {
                "type": _cc(f"{_TERM}/coverage-class", "group", "Group"),
                "value": row["group_id"],
                "name": row["group_name"],
            }
        )
    classes.append(
        {
            "type": _cc(f"{_TERM}/coverage-class", "plan", "Plan"),
            "value": row["plan_id"],
            "name": row["plan_name"],
        }
    )
    res: Resource = {
        "resourceType": "Coverage",
        "id": coverage_id(row),
        "identifier": [_ident(SYS_ELIGIBILITY, row["eligibility_id"])]
        if row["eligibility_id"]
        else [],
        "status": "cancelled" if ended else "active",
        "type": _cc(f"{_TERM}/v3-ActCode", *ptype) if ptype else None,
        "subscriber": subscriber,
        "subscriberId": subscriber_id,
        "beneficiary": patient_ref(member_id),
        "dependent": member["member_suffix"] if member and rel_code != "18" else None,
        "relationship": _cc(
            f"{_TERM}/subscriber-relationship",
            _RELATIONSHIP.get(rel_code or "", "other"),
        )
        if rel_code
        else None,
        "period": {"start": iso(row["coverage_start"]), "end": iso(end)},
        "payor": [_payer_ref(row["payer_id"], row["payer_name"])],
        "class": classes,
    }
    return _clean(res)  # type: ignore[no-any-return]


# --- Practitioner / Organization ---------------------------------------------------------------


def practitioner(row: Mapping[str, Any]) -> Resource:
    """An entity-type-1 provider row as a US Core-shaped Practitioner."""
    qualification = (
        [{"code": {**_cc(SYS_TAXONOMY, row["taxonomy_code"]), "text": row["specialty"]}}]
        if row["taxonomy_code"]
        else []
    )
    res: Resource = {
        "resourceType": "Practitioner",
        "id": make_id(row["npi"]),
        "meta": {"profile": [f"{US_CORE}/us-core-practitioner"]},
        "identifier": [_ident(SYS_NPI, row["npi"])],
        "active": True,
        "name": [
            {
                "family": row["last_name"],
                "given": [row["first_name"]] if row["first_name"] else [],
            }
        ],
        "address": _address(row, "work"),
        "qualification": qualification,
    }
    return _clean(res)  # type: ignore[no-any-return]


def organization(row: Mapping[str, Any]) -> Resource:
    """An entity-type-2 provider row as a US Core-shaped Organization."""
    identifiers: list[dict[str, Any]] = [_ident(SYS_NPI, row["npi"])]
    if row["tax_id"]:
        identifiers.append(
            {
                "type": _cc(f"{_TERM}/v2-0203", "TAX", "Tax ID number"),
                **_ident(SYS_EIN, row["tax_id"]),
            }
        )
    if row["ncpdp_id"]:
        identifiers.append(_ident(SYS_NCPDP_ID, row["ncpdp_id"]))
    res: Resource = {
        "resourceType": "Organization",
        "id": make_id(row["npi"]),
        "meta": {"profile": [f"{US_CORE}/us-core-organization"]},
        "identifier": identifiers,
        "active": True,
        "type": [_cc(f"{_TERM}/organization-type", "prov", "Healthcare Provider")],
        "name": row["org_name"] or row["npi"],
        "address": _address(row, "work"),
    }
    return _clean(res)  # type: ignore[no-any-return]


def provider_resource(row: Mapping[str, Any]) -> Resource:
    """Practitioner for entity type 1, Organization for entity type 2."""
    if row["entity_type"] == "1":
        return practitioner(row)
    if row["entity_type"] == "2":
        return organization(row)
    raise ContractError(
        f"provider {row['npi']}: entity_type {row['entity_type']!r} must be '1' or '2'"
    )


def payer_organizations(tables: TableSet) -> list[Resource]:
    """One Organization per distinct payer in the eligibility and claim tables."""
    seen: dict[str, str | None] = {}
    for name in ("eligibility", "medical_claim"):
        for r in tables.rows(name):
            if r["payer_id"] and (r["payer_id"] not in seen or not seen[r["payer_id"]]):
                seen[r["payer_id"]] = r["payer_name"]
    return [
        _clean(
            {
                "resourceType": "Organization",
                "id": payer_org_id(pid),
                "meta": {"profile": [f"{US_CORE}/us-core-organization"]},
                "identifier": [_ident(SYS_PAYER, pid)],
                "active": True,
                "type": [_cc(f"{_TERM}/organization-type", "pay", "Payer")],
                "name": name or pid,
            }
        )
        for pid, name in seen.items()
    ]


# --- Claim and ExplanationOfBenefit ------------------------------------------------------------


def _claim_type(claim: Mapping[str, Any]) -> str:
    return "institutional" if claim["claim_type"] == "I" else "professional"


def _procedure_coding(code: str) -> dict[str, str]:
    """CPT for five digits (and Category II/III), HCPCS Level II otherwise."""
    cpt = re.fullmatch(r"[0-9]{4}[0-9FTU]", code) is not None
    return _coding(SYS_CPT if cpt else SYS_HCPCS, code)


def _modifiers(line: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Numeric modifiers are CPT; lettered ones (GP, QW, ...) are HCPCS Level II."""
    return [
        _cc(SYS_CPT if line[f"modifier_{i}"].isdigit() else SYS_HCPCS, line[f"modifier_{i}"])
        for i in range(1, 5)
        if line.get(f"modifier_{i}")
    ]


def _product_or_service(line: Mapping[str, Any]) -> dict[str, Any]:
    if line["procedure_code"]:
        return {"coding": [_procedure_coding(line["procedure_code"])]}
    return _cc(f"{_TERM}/data-absent-reason", "not-applicable", "Not Applicable")


def _diagnoses(claim: Mapping[str, Any], ctx: Context) -> list[dict[str, Any]]:
    out = []
    for d in sorted(ctx.diagnoses.get(claim["claim_id"], []), key=lambda r: r["sequence"]):
        entry: dict[str, Any] = {
            "sequence": d["sequence"],
            "diagnosisCodeableConcept": _cc(SYS_ICD10CM, icd10cm_code(d["icd10_code"])),
        }
        if d["diagnosis_type"] in ("principal", "admitting"):
            entry["type"] = [_cc(f"{_TERM}/ex-diagnosistype", d["diagnosis_type"])]
        poa = (d["poa_indicator"] or "").strip().upper()
        if poa in ("Y", "N", "U", "W"):
            entry["onAdmission"] = _cc(SYS_POA, poa)
        if d["sequence"] == 1 and claim["drg_code"]:
            entry["packageCode"] = _cc(SYS_MSDRG, claim["drg_code"])
        out.append(entry)
    return out


def _procedures(claim: Mapping[str, Any], ctx: Context) -> list[dict[str, Any]]:
    return [
        {
            "sequence": p["sequence"],
            "date": iso(p["procedure_date"]),
            "procedureCodeableConcept": _cc(SYS_ICD10PCS, p["icd10pcs_code"]),
        }
        for p in sorted(ctx.procedures.get(claim["claim_id"], []), key=lambda r: r["sequence"])
    ]


def _care_team(claim: Mapping[str, Any], ctx: Context) -> list[dict[str, Any]]:
    team = []
    if claim["claim_type"] == "I":
        if claim["attending_npi"]:
            team.append(
                {
                    "provider": ctx.provider_ref(claim["attending_npi"]),
                    "role": _cc(SYS_CARIN_CARE_TEAM_ROLE, "attending", "Attending"),
                }
            )
    elif claim["rendering_npi"]:
        team.append(
            {
                "provider": ctx.provider_ref(claim["rendering_npi"]),
                "role": _cc(f"{_TERM}/claimcareteamrole", "primary", "Primary provider"),
            }
        )
    return [{"sequence": i, **t} for i, t in enumerate(team, 1)]


def _supporting_info(claim: Mapping[str, Any]) -> list[dict[str, Any]]:
    info: list[dict[str, Any]] = []
    if claim["admission_date"] or claim["discharge_date"]:
        info.append(
            {
                "category": _cc(SYS_CARIN_SUPPORTING_INFO, "admissionperiod"),
                "timingPeriod": {
                    "start": iso(claim["admission_date"]),
                    "end": iso(claim["discharge_date"]),
                },
            }
        )
    if claim["received_date"]:
        info.append(
            {
                "category": _cc(SYS_CARIN_SUPPORTING_INFO, "clmrecvddate"),
                "timingDate": iso(claim["received_date"]),
            }
        )
    if claim["type_of_bill"]:
        info.append(
            {
                "category": _cc(SYS_CARIN_SUPPORTING_INFO, "typeofbill"),
                "code": _cc(SYS_TYPE_OF_BILL, claim["type_of_bill"]),
            }
        )
    if claim["admission_type_code"]:
        info.append(
            {
                "category": _cc(f"{_TERM}/claiminformationcategory", "info"),
                "code": _cc(SYS_ADMIT_TYPE, claim["admission_type_code"]),
            }
        )
    if claim["patient_status_code"]:
        info.append(
            {
                "category": _cc(f"{_TERM}/claiminformationcategory", "discharge"),
                "code": _cc(SYS_DISCHARGE, claim["patient_status_code"]),
            }
        )
    return [{"sequence": i, **x} for i, x in enumerate(info, 1)]


def _pos(code: str | None) -> dict[str, Any] | None:
    return _cc(SYS_POS, code) if code else None


def _item_core(
    claim: Mapping[str, Any], line: Mapping[str, Any], valid_dx: set[int], has_team: bool
) -> dict[str, Any]:
    pointers = [
        int(p)
        for p in (line["diagnosis_pointers"] or "").replace(" ", "").split(",")
        if p.isdigit()
    ]
    item: dict[str, Any] = {
        "sequence": line["line_number"],
        "careTeamSequence": [1] if has_team else [],
        "diagnosisSequence": [p for p in pointers if p in valid_dx],
        "revenue": _cc(SYS_REVENUE, line["revenue_code"]) if line["revenue_code"] else None,
        "productOrService": _product_or_service(line),
        "modifier": _modifiers(line),
        "servicedDate": iso(line["service_date"]),
        "locationCodeableConcept": _pos(line["place_of_service"] or claim["place_of_service"])
        if claim["claim_type"] != "I"
        else None,
        "quantity": {"value": _qty(line["units"] if line["units"] is not None else 1.0)},
        "net": _amount(line["billed_amount"]),
    }
    return item


def _sorted_lines(claim: Mapping[str, Any], ctx: Context) -> list[Mapping[str, Any]]:
    return sorted(ctx.lines.get(claim["claim_id"], []), key=lambda r: r["line_number"])


def _insurance(claim: Mapping[str, Any], ctx: Context) -> list[dict[str, Any]]:
    return [
        {
            "sequence": 1,
            "focal": True,
            "coverage": ctx.coverage_ref(
                claim["member_id"], claim["service_from_date"], claim["plan_id"]
            ),
            "preAuthRef": [claim["prior_auth_number"]] if claim["prior_auth_number"] else [],
        }
    ]


def claim_id(claim_id_value: str) -> str:
    return make_id(claim_id_value)


def eob_id(claim_id_value: str) -> str:
    return make_id(claim_id_value, "eob-")


def _related(claim: Mapping[str, Any], ctx: Context) -> list[dict[str, Any]]:
    orig = claim["original_claim_id"]
    if not orig:
        return []
    ref: dict[str, Any] = (
        {"reference": f"Claim/{claim_id(orig)}"}
        if orig in ctx.claims
        else {"identifier": _ident(SYS_CLAIM, orig)}
    )
    return [
        {
            "claim": ref,
            "relationship": _cc(f"{_TERM}/ex-relatedclaimrelationship", "prior", "Prior Claim"),
        }
    ]


def _billable_period(claim: Mapping[str, Any]) -> dict[str, str | None]:
    return {"start": iso(claim["service_from_date"]), "end": iso(claim["service_to_date"])}


def claim(row: Mapping[str, Any], ctx: Context) -> Resource:
    """A medical claim (with its lines, diagnoses and procedures) as a Claim."""
    diagnoses = _diagnoses(row, ctx)
    team = _care_team(row, ctx)
    valid_dx = {d["sequence"] for d in diagnoses}
    res: Resource = {
        "resourceType": "Claim",
        "id": claim_id(row["claim_id"]),
        "identifier": [
            _ident(SYS_CLAIM, row["claim_id"]),
            *(
                [_ident(SYS_PAYER_CLAIM, row["payer_claim_number"])]
                if row["payer_claim_number"]
                else []
            ),
        ],  # fmt: skip
        "status": "cancelled" if row["claim_frequency_code"] == "8" else "active",
        "type": _cc(SYS_CLAIM_TYPE, _claim_type(row)),
        "subType": _cc(SYS_TYPE_OF_BILL, row["type_of_bill"]) if row["type_of_bill"] else None,
        "use": "claim",
        "patient": patient_ref(row["member_id"]),
        "billablePeriod": _billable_period(row),
        "created": iso(row["received_date"] or row["service_from_date"]),
        "provider": ctx.provider_ref(row["billing_npi"]),
        "priority": _cc(f"{_TERM}/processpriority", "normal"),
        "related": _related(row, ctx),
        "careTeam": team,
        "supportingInfo": _supporting_info(row),
        "diagnosis": diagnoses,
        "procedure": _procedures(row, ctx),
        "insurance": _insurance(row, ctx),
        "item": [_item_core(row, ln, valid_dx, bool(team)) for ln in _sorted_lines(row, ctx)],
        "total": _amount(row["total_billed"]),
    }
    res["facility"] = ctx.facility_ref(row["facility_npi"])
    return _clean(res)  # type: ignore[no-any-return]


def _adjudication(category: tuple[str, str], value: float | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {"category": _cc(*category), "amount": _amount(value)}


def _cat(code: str, carin: bool = False) -> tuple[str, str]:
    return (SYS_CARIN_ADJUDICATION if carin else SYS_ADJUDICATION, code)


def _adjudications(
    billed: float | None,
    allowed: float | None,
    paid: float | None,
    copay: float | None,
    coinsurance: float | None,
    deductible: float | None,
    carc: str | None = None,
    rarc: str | None = None,
) -> list[dict[str, Any]]:
    """Adjudication entries; denial reasons are CARC/RARC codes in ``reason``."""
    out = [
        _adjudication(_cat("submitted"), billed),
        _adjudication(_cat("eligible"), allowed),
        _adjudication(_cat("benefit"), paid),
        _adjudication(_cat("copay"), copay),
        _adjudication(_cat("coinsurance", True), coinsurance),
        _adjudication(_cat("deductible"), deductible),
    ]
    # A CARC/RARC explains why an amount was not covered: one "noncovered" entry per code.
    for system, code in ((SYS_CARC, carc), (SYS_RARC, rarc)):
        if code:
            out.append({"category": _cc(*_cat("noncovered", True)), "reason": _cc(system, code)})
    return [a for a in out if a]


def _outcome(row: Mapping[str, Any], lines: list[Mapping[str, Any]]) -> tuple[str, str, str]:
    """``(outcome, status, disposition)`` of the adjudicated claim."""
    status = row["claim_status"]
    denied_lines = [ln for ln in lines if ln["line_status"] == "denied"]
    if status == "denied" or (lines and len(denied_lines) == len(lines)):
        return "error", "active", "Claim denied"
    if status == "pending":
        return "queued", "active", "Claim pending adjudication"
    if status == "reversed":
        return "complete", "cancelled", "Claim reversed"
    if denied_lines:
        return "partial", "active", "Claim paid with denied service lines"
    return "complete", "active", "Claim adjudicated"


def explanation_of_benefit(row: Mapping[str, Any], ctx: Context) -> Resource:
    """The adjudication of a medical claim as an ExplanationOfBenefit."""
    lines = _sorted_lines(row, ctx)
    outcome, status, disposition = _outcome(row, lines)
    diagnoses = _diagnoses(row, ctx)
    team = _care_team(row, ctx)
    valid_dx = {d["sequence"] for d in diagnoses}
    items = []
    for ln in lines:
        item = _item_core(row, ln, valid_dx, bool(team))
        item.pop("net", None)
        item["adjudication"] = _adjudications(
            ln["billed_amount"],
            ln["allowed_amount"],
            ln["paid_amount"],
            ln["copay"],
            ln["coinsurance"],
            ln["deductible"],
            ln["denial_carc"],
            ln["denial_rarc"],
        )
        items.append(item)
    totals = [
        (_cat("submitted"), row["total_billed"]),
        (_cat("eligible"), row["total_allowed"]),
        (_cat("benefit"), row["total_paid"]),
        (_cat("copay"), row["member_copay"]),
        (_cat("coinsurance", True), row["member_coinsurance"]),
        (_cat("deductible"), row["member_deductible"]),
    ]
    payment = (
        {"amount": _amount(row["total_paid"]), "date": iso(row["adjudication_date"])}
        if row["total_paid"] is not None
        else None
    )
    res: Resource = {
        "resourceType": "ExplanationOfBenefit",
        "id": eob_id(row["claim_id"]),
        "identifier": [_ident(SYS_CLAIM, row["claim_id"])],
        "status": status,
        "type": _cc(SYS_CLAIM_TYPE, _claim_type(row)),
        "subType": _cc(SYS_TYPE_OF_BILL, row["type_of_bill"]) if row["type_of_bill"] else None,
        "use": "claim",
        "patient": patient_ref(row["member_id"]),
        "billablePeriod": _billable_period(row),
        "created": iso(
            row["adjudication_date"] or row["received_date"] or row["service_from_date"]
        ),
        "insurer": _payer_ref(row["payer_id"], row["payer_name"]),
        "provider": ctx.provider_ref(row["billing_npi"]),
        "related": _related(row, ctx),
        "claim": {"reference": f"Claim/{claim_id(row['claim_id'])}"},
        "outcome": outcome,
        "disposition": disposition,
        "careTeam": team,
        "supportingInfo": _supporting_info(row),
        "diagnosis": diagnoses,
        "procedure": _procedures(row, ctx),
        "insurance": [
            {
                "focal": True,
                "coverage": ctx.coverage_ref(
                    row["member_id"], row["service_from_date"], row["plan_id"]
                ),
            }
        ],
        "item": items,
        "total": [
            {"category": _cc(*cat), "amount": _amount(v)} for cat, v in totals if v is not None
        ],
        "payment": payment,
    }
    res["facility"] = ctx.facility_ref(row["facility_npi"])
    return _clean(res)  # type: ignore[no-any-return]


# --- MedicationDispense ------------------------------------------------------------------------

_DISPENSE_STATUS = {"reversed": "entered-in-error", "rejected": "cancelled"}


def medication_dispense(row: Mapping[str, Any], ctx: Context) -> Resource:
    """A pharmacy claim as a MedicationDispense: completed, entered-in-error (reversed) or
    cancelled (rejected, with the reject code as the status reason; R4 has no ``not-done``)."""
    drug = ctx.drugs.get(row["ndc"])
    name = row["drug_name"] or (drug["drug_name"] if drug else None)
    codings = [_coding(SYS_NDC, row["ndc"])]
    if drug and drug["rxnorm_code"]:
        codings.append(_coding(SYS_RXNORM, drug["rxnorm_code"]))
    status = _DISPENSE_STATUS.get(row["claim_status"] or "", "completed")
    performer = None
    if row["pharmacy_npi"]:
        performer = ctx.provider_ref(row["pharmacy_npi"])
    elif row["pharmacy_ncpdp_id"]:
        performer = {"identifier": _ident(SYS_NCPDP_ID, row["pharmacy_ncpdp_id"])}
    res: Resource = {
        "resourceType": "MedicationDispense",
        "id": make_id(row["rx_claim_id"]),
        "identifier": [
            _ident(SYS_RX_CLAIM, row["rx_claim_id"]),
            *([_ident(SYS_RX_NUMBER, row["rx_number"])] if row["rx_number"] else []),
        ],
        "status": status,
        "statusReasonCodeableConcept": _cc(SYS_NCPDP_REJECT, row["reject_code"])
        if status == "cancelled" and row["reject_code"]
        else None,
        "medicationCodeableConcept": {"coding": codings, "text": name},
        "subject": patient_ref(row["member_id"]),
        "performer": [{"actor": performer}] if performer else [],
        "quantity": {"value": _qty(row["quantity"])},
        "daysSupply": {
            "value": row["days_supply"],
            "unit": "days",
            "system": "http://unitsofmeasure.org",
            "code": "d",
        },
        "whenHandedOver": iso(row["fill_date"]),
    }
    return _clean(res)  # type: ignore[no-any-return]


# --- Table dispatch ----------------------------------------------------------------------------

#: Resource types written for each contract table, in output order.
TABLE_RESOURCES: dict[str, tuple[str, ...]] = {
    "member": ("Patient",),
    "eligibility": ("Coverage",),
    "provider": ("Practitioner", "Organization"),
    "medical_claim": ("Claim", "ExplanationOfBenefit"),
    "pharmacy_claim": ("MedicationDispense",),
}


def resources_for(table: str, tables: TableSet) -> Iterator[Resource]:
    """Every resource of one primary table, row by row, in table order.

    Raises :class:`ContractError` for a table with no FHIR mapping.
    """
    if table not in TABLE_RESOURCES:
        raise ContractError(
            f"table {table!r} has no FHIR R4 mapping; tables: {sorted(TABLE_RESOURCES)}"
        )
    ctx = Context(tables)
    for row in tables.rows(table):
        if table == "member":
            yield patient(row)
        elif table == "eligibility":
            yield coverage(row, ctx)
        elif table == "provider":
            yield provider_resource(row)
        elif table == "medical_claim":
            yield claim(row, ctx)
            yield explanation_of_benefit(row, ctx)
        else:
            yield medication_dispense(row, ctx)
