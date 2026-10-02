"""Payer tables to OMOP CDM v5.4 rows.

Identifiers
-----------
Every ``*_id`` is a deterministic positive 31-bit integer (the v5.4 DDL types them ``integer``):
a BLAKE2 hash of ``namespace`` and the natural key, probed forward on a collision with keys
taken in sorted order. The same input gives the same ids on every run and platform.

Concepts
--------
No vocabulary is shipped. Standard concept ids are written only where this module fixes them
(gender, race, ethnicity, visit type, currency and the type concepts below) or where the caller's
``concept_map`` supplies them; every other ``*_concept_id`` is ``0`` and the matching source
value keeps the original code. ``concept_map`` maps a source value to a concept id, either as
``"<domain>:<value>"`` (domains ``condition``, ``procedure``, ``drug``, ``route``, ``specialty``)
or as the bare value.

Type concepts (OMOP "Type Concept" vocabulary): ``32813`` Claim enrolment record
(observation period, payer plan period, death), ``32810`` Claim (visits, conditions,
procedures), ``32869`` Pharmacy claim (drug exposure) and ``32814`` Cost record (cost).

Scope
-----
Rows are written only for persons present in the ``member`` table. Medical claims that are
reversed, voided (frequency code 8) or replaced by a later claim (``original_claim_id``) are
left out, as are pharmacy claims whose status is not ``paid``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from ..common import TableSet, money
from . import ddl

Row = dict[str, Any]

TYPE_ENROLMENT = 32813
TYPE_CLAIM = 32810
TYPE_PHARMACY_CLAIM = 32869
TYPE_COST = 32814
CURRENCY_USD = 44818668

GENDER = {"M": 8507, "F": 8532}
RACE = {
    "white": 8527,
    "black or african american": 8516,
    "asian": 8515,
    "american indian or alaska native": 8657,
    "native hawaiian or other pacific islander": 8557,
}
ETHNICITY = {"hispanic or latino": 38003563, "not hispanic or latino": 38003564}

_ID_SPACE = 2**31 - 2

_DATE_COLUMNS = (
    ("eligibility", "coverage_start"),
    ("eligibility", "coverage_end"),
    ("medical_claim", "service_to_date"),
    ("medical_claim", "discharge_date"),
    ("medical_claim_line", "service_date"),
    ("claim_procedure", "procedure_date"),
    ("pharmacy_claim", "fill_date"),
)


def allocate(namespace: str, keys: Iterable[str]) -> dict[str, int]:
    """Deterministic 31-bit ids for distinct ``keys``; hash collisions probe forward."""
    out: dict[str, int] = {}
    used: set[int] = set()
    for key in sorted(set(keys)):
        digest = hashlib.blake2b(f"{namespace}\x1f{key}".encode(), digest_size=8).digest()
        n = int.from_bytes(digest, "big") % _ID_SPACE + 1
        while n in used:
            n = n % _ID_SPACE + 1
        used.add(n)
        out[key] = n
    return out


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _dec(value: float | Decimal | None) -> Decimal | None:
    return None if value is None else money(value)


def _sum(*values: float | None) -> Decimal | None:
    present = [v for v in values if v is not None]
    return None if not present else sum((money(v) for v in present), Decimal("0.00"))


def _dotted(code: str) -> str:
    return code if len(code) <= 3 else f"{code[:3]}.{code[3:]}"


def _midnight(day: dt.date | None) -> dt.datetime | None:
    return None if day is None else dt.datetime(day.year, day.month, day.day)


class _Concepts:
    def __init__(self, concept_map: Mapping[str, int] | None) -> None:
        self._map = dict(concept_map or {})

    def get(self, domain: str, *values: str | None) -> int:
        for value in values:
            if value is None:
                continue
            for key in (f"{domain}:{value}", value):
                if key in self._map:
                    return int(self._map[key])
        return 0


def _horizon(ts: TableSet) -> dt.date | None:
    """The latest date in the input, used as the end of an open coverage span."""
    best: dt.date | None = None
    for name, col in _DATE_COLUMNS:
        if not ts.has(name):
            continue
        value = pc.max(ts.tables[name].column(col)).as_py()
        if value is not None and (best is None or value > best):
            best = value
    if ts.has("pharmacy_claim"):
        for r in ts.rows("pharmacy_claim"):
            if (r["claim_status"] or "").lower() != "paid":
                continue
            days = r["days_supply"] or 0
            end = r["fill_date"] + dt.timedelta(days=max(days - 1, 0))
            if best is None or end > best:
                best = end
    return best


def map_tables(ts: TableSet, concept_map: Mapping[str, int] | None = None) -> dict[str, list[Row]]:
    """OMOP rows per table name, for every table derivable from the tables in ``ts``."""
    return _Mapper(ts, _Concepts(concept_map)).run()


class _Mapper:
    def __init__(self, ts: TableSet, concepts: _Concepts) -> None:
        self.ts = ts
        self.cm = concepts
        self.out: dict[str, list[Row]] = {}
        self.horizon = _horizon(ts)
        self.provider_ids: dict[str, int] = {}
        self.ppp_by_person: dict[int, list[Row]] = {}
        self.visit: dict[str, Row] = {}
        self.drug: dict[str, Row] = {}

    def run(self) -> dict[str, list[Row]]:
        ts = self.ts
        self._locations_and_care_sites()
        self._providers()
        self._persons()
        if ts.has("eligibility"):
            self._observation_and_payer_periods()
        if ts.has("medical_claim"):
            self._visits()
            if ts.has("claim_diagnosis"):
                self._conditions()
            if ts.has("medical_claim_line") or ts.has("claim_procedure"):
                self._procedures()
        if ts.has("pharmacy_claim"):
            self._drugs()
        if ts.has("medical_claim") or ts.has("pharmacy_claim"):
            self._costs()
        for rows in self.out.values():
            rows.sort(key=lambda r: r[next(iter(r))])
        return self.out

    def _locations_and_care_sites(self) -> None:
        ts = self.ts
        providers = ts.rows("provider")
        facility = self._facility_npis()
        loc_rows: dict[str, Row] = {}

        def location(row: Row) -> str | None:
            parts = [
                _text(row.get("address_line1")),
                _text(row.get("address_line2")),
                _text(row.get("city")),
                _text(row.get("state")),
                _text(row.get("zip")),
            ]
            if not any(parts):
                return None
            key = "|".join(p or "" for p in parts)
            loc_rows.setdefault(
                key,
                {
                    "address_1": parts[0],
                    "address_2": parts[1],
                    "city": parts[2],
                    "state": parts[3],
                    "zip": parts[4],
                    "country_concept_id": 0,
                },
            )
            return key

        self.person_loc = {r["member_id"]: location(r) for r in ts.rows("member")}
        self.site_loc = {p["npi"]: location(p) for p in providers if p["npi"] in facility}
        ids = allocate("location", loc_rows)
        self.loc_id = {k: ids[k] for k in loc_rows}
        self.out["location"] = [{"location_id": self.loc_id[k], **loc_rows[k]} for k in loc_rows]
        self.site_ids = allocate("care_site", self.site_loc)
        if ts.has("provider"):
            self.out["care_site"] = []
            by_npi = ts.index("provider", "npi")
            for npi, site_id in self.site_ids.items():
                p = by_npi[npi]
                key = self.site_loc[npi]
                self.out["care_site"].append(
                    {
                        "care_site_id": site_id,
                        "care_site_name": _text(p["org_name"]) or self._person_name(p),
                        "place_of_service_concept_id": 0,
                        "location_id": None if key is None else self.loc_id[key],
                        "care_site_source_value": npi,
                    }
                )

    @staticmethod
    def _person_name(p: Row) -> str | None:
        return _text(" ".join(x for x in (p.get("first_name"), p.get("last_name")) if x))

    def _facility_npis(self) -> set[str]:
        ts = self.ts
        out: set[str] = set()
        for c in ts.rows("medical_claim"):
            if c["facility_npi"]:
                out.add(c["facility_npi"])
            if c["claim_type"] == "I" and c["billing_npi"]:
                out.add(c["billing_npi"])
        return out

    def _providers(self) -> None:
        ts = self.ts
        if not ts.has("provider"):
            return
        rows = ts.rows("provider")
        self.provider_ids = allocate("provider", (p["npi"] for p in rows))
        out: list[Row] = []
        for p in rows:
            name = _text(p["org_name"]) or self._person_name(p)
            out.append(
                {
                    "provider_id": self.provider_ids[p["npi"]],
                    "provider_name": name,
                    "npi": p["npi"],
                    "specialty_concept_id": self.cm.get(
                        "specialty", _text(p["taxonomy_code"]), _text(p["specialty"])
                    ),
                    "care_site_id": self.site_ids.get(p["npi"]),
                    "provider_source_value": p["npi"],
                    "specialty_source_value": _text(p["taxonomy_code"]),
                    "specialty_source_concept_id": 0,
                }
            )
        self.out["provider"] = out

    def _persons(self) -> None:
        ts = self.ts
        members = ts.rows("member")
        self.person_ids = allocate("person", (m["member_id"] for m in members))
        pcp: dict[str, str] = {}
        for e in sorted(ts.rows("eligibility"), key=lambda r: (r["coverage_start"], r["plan_id"])):
            if e["pcp_npi"] in self.provider_ids:
                pcp[e["member_id"]] = e["pcp_npi"]
        persons: list[Row] = []
        deaths: list[Row] = []
        for m in members:
            b = m["birth_date"]
            sex = (m["sex"] or "").upper()
            key = self.person_loc[m["member_id"]]
            pid = self.person_ids[m["member_id"]]
            persons.append(
                {
                    "person_id": pid,
                    "gender_concept_id": GENDER.get(sex, 0),
                    "year_of_birth": b.year,
                    "month_of_birth": b.month,
                    "day_of_birth": b.day,
                    "birth_datetime": _midnight(b),
                    "race_concept_id": RACE.get((m["race"] or "").lower(), 0),
                    "ethnicity_concept_id": ETHNICITY.get((m["ethnicity"] or "").lower(), 0),
                    "location_id": None if key is None else self.loc_id[key],
                    "provider_id": self.provider_ids.get(pcp.get(m["member_id"], "")),
                    "person_source_value": m["member_id"],
                    "gender_source_value": _text(m["sex"]),
                    "gender_source_concept_id": 0,
                    "race_source_value": _text(m["race"]),
                    "race_source_concept_id": 0,
                    "ethnicity_source_value": _text(m["ethnicity"]),
                    "ethnicity_source_concept_id": 0,
                }
            )
            if m["deceased_date"] is not None:
                deaths.append(
                    {
                        "person_id": pid,
                        "death_date": m["deceased_date"],
                        "death_type_concept_id": TYPE_ENROLMENT,
                        "cause_concept_id": 0,
                        "cause_source_concept_id": 0,
                    }
                )
        self.out["person"] = persons
        if deaths:
            self.out["death"] = deaths

    def _span_end(self, start: dt.date, end: dt.date | None) -> dt.date:
        if end is not None:
            return end
        return max(start, self.horizon or start)

    def _observation_and_payer_periods(self) -> None:
        ts = self.ts
        spans = [e for e in ts.rows("eligibility") if e["member_id"] in self.person_ids]
        family = {m["member_id"]: m["subscriber_id"] for m in ts.rows("member")}

        keyed: dict[str, Row] = {}
        for e in sorted(spans, key=lambda r: (r["member_id"], r["coverage_start"], r["plan_id"])):
            base = e["eligibility_id"] or f"{e['member_id']}|{e['plan_id']}|{e['coverage_start']}"
            key, n = base, 1
            while key in keyed:
                n += 1
                key = f"{base}#{n}"
            keyed[key] = e
        ids = allocate("payer_plan_period", keyed)
        ppp: list[Row] = []
        for key, e in keyed.items():
            row = {
                "payer_plan_period_id": ids[key],
                "person_id": self.person_ids[e["member_id"]],
                "payer_plan_period_start_date": e["coverage_start"],
                "payer_plan_period_end_date": self._span_end(
                    e["coverage_start"], e["coverage_end"]
                ),
                "payer_concept_id": 0,
                "payer_source_value": _text(e["payer_name"]) or _text(e["payer_id"]),
                "payer_source_concept_id": 0,
                "plan_concept_id": 0,
                "plan_source_value": _text(e["plan_id"]),
                "plan_source_concept_id": 0,
                "sponsor_concept_id": 0,
                "sponsor_source_value": _text(e["group_id"]),
                "sponsor_source_concept_id": 0,
                "family_source_value": _text(family.get(e["member_id"])),
                "stop_reason_concept_id": 0,
                "stop_reason_source_value": _text(e["termination_reason"]),
                "stop_reason_source_concept_id": 0,
                "_plan": e["plan_id"],
            }
            ppp.append(row)
            self.ppp_by_person.setdefault(row["person_id"], []).append(row)

        health = [e for e in spans if e["insurance_line"] in (None, "HLT")]
        merged: dict[str, list[list[dt.date]]] = {}
        for e in sorted(health, key=lambda r: (r["member_id"], r["coverage_start"])):
            start, end = e["coverage_start"], self._span_end(e["coverage_start"], e["coverage_end"])
            cur = merged.setdefault(e["member_id"], [])
            if cur and start <= cur[-1][1] + dt.timedelta(days=1):
                cur[-1][1] = max(cur[-1][1], end)
            else:
                cur.append([start, end])
        op_keys = {f"{m}|{s}": (m, s, en) for m, periods in merged.items() for s, en in periods}
        op_ids = allocate("observation_period", op_keys)
        self.out["observation_period"] = [
            {
                "observation_period_id": op_ids[k],
                "person_id": self.person_ids[m],
                "observation_period_start_date": s,
                "observation_period_end_date": en,
                "period_type_concept_id": TYPE_ENROLMENT,
            }
            for k, (m, s, en) in op_keys.items()
        ]
        self.out["payer_plan_period"] = ppp

    def _included_claims(self) -> list[Row]:
        claims = self.ts.rows("medical_claim")
        superseded = {c["original_claim_id"] for c in claims if c["original_claim_id"]}
        return [
            c
            for c in claims
            if c["member_id"] in self.person_ids
            and (c["claim_status"] or "").lower() != "reversed"
            and c["claim_frequency_code"] != "8"
            and c["claim_id"] not in superseded
        ]

    @staticmethod
    def _visit_concept(c: Row) -> int:
        if c["claim_type"] == "I":
            return 9201 if c["admission_date"] else 9202
        return {"21": 9201, "23": 9203}.get(c["place_of_service"] or "", 9202)

    def _visits(self) -> None:
        claims = self._included_claims()
        self.visit_ids = allocate("visit_occurrence", (c["claim_id"] for c in claims))
        out: list[Row] = []
        for c in claims:
            start = c["admission_date"] or c["service_from_date"]
            end = max(start, c["discharge_date"] or c["service_to_date"])
            npi = c["attending_npi"] if c["claim_type"] == "I" else c["rendering_npi"]
            row = {
                "visit_occurrence_id": self.visit_ids[c["claim_id"]],
                "person_id": self.person_ids[c["member_id"]],
                "visit_concept_id": self._visit_concept(c),
                "visit_start_date": start,
                "visit_start_datetime": _midnight(start),
                "visit_end_date": end,
                "visit_end_datetime": _midnight(end),
                "visit_type_concept_id": TYPE_CLAIM,
                "provider_id": self.provider_ids.get(npi or ""),
                "care_site_id": self.site_ids.get(c["facility_npi"] or ""),
                "visit_source_value": c["claim_id"],
                "visit_source_concept_id": 0,
                "discharged_to_concept_id": 0 if c["patient_status_code"] else None,
                "discharged_to_source_value": _text(c["patient_status_code"]),
            }
            out.append(row)
            self.visit[c["claim_id"]] = row
        self.out["visit_occurrence"] = out

    def _conditions(self) -> None:
        rows = [d for d in self.ts.rows("claim_diagnosis") if d["claim_id"] in self.visit]
        ids = allocate("condition_occurrence", (f"{d['claim_id']}|{d['sequence']}" for d in rows))
        out: list[Row] = []
        for d in rows:
            v = self.visit[d["claim_id"]]
            code = d["icd10_code"]
            out.append(
                {
                    "condition_occurrence_id": ids[f"{d['claim_id']}|{d['sequence']}"],
                    "person_id": v["person_id"],
                    "condition_concept_id": self.cm.get("condition", _dotted(code), code),
                    "condition_start_date": v["visit_start_date"],
                    "condition_type_concept_id": TYPE_CLAIM,
                    "provider_id": v["provider_id"],
                    "visit_occurrence_id": v["visit_occurrence_id"],
                    "condition_source_value": _dotted(code),
                    "condition_source_concept_id": 0,
                    "condition_status_source_value": _text(d["diagnosis_type"]),
                }
            )
        self.out["condition_occurrence"] = out

    def _procedures(self) -> None:
        ts = self.ts
        entries: list[tuple[str, Row, Row]] = []
        for ln in ts.rows("medical_claim_line"):
            if ln["procedure_code"] and ln["claim_id"] in self.visit:
                entries.append(
                    (f"L|{ln['claim_id']}|{ln['line_number']}", ln, self.visit[ln["claim_id"]])
                )
        for p in ts.rows("claim_procedure"):
            if p["claim_id"] in self.visit:
                entries.append((f"P|{p['claim_id']}|{p['sequence']}", p, self.visit[p["claim_id"]]))
        ids = allocate("procedure_occurrence", (k for k, _, _ in entries))
        out: list[Row] = []
        for key, src, v in entries:
            if key.startswith("L|"):
                code = src["procedure_code"]
                mods = ",".join(m for m in (src[f"modifier_{i}"] for i in range(1, 5)) if m)
                day = src["service_date"]
                qty = None if src["units"] is None else round(src["units"])
            else:
                code = src["icd10pcs_code"]
                mods = ""
                day = src["procedure_date"] or v["visit_start_date"]
                qty = None
            out.append(
                {
                    "procedure_occurrence_id": ids[key],
                    "person_id": v["person_id"],
                    "procedure_concept_id": self.cm.get("procedure", code),
                    "procedure_date": day,
                    "procedure_type_concept_id": TYPE_CLAIM,
                    "quantity": qty,
                    "provider_id": v["provider_id"],
                    "visit_occurrence_id": v["visit_occurrence_id"],
                    "procedure_source_value": code,
                    "procedure_source_concept_id": 0,
                    "modifier_source_value": mods or None,
                }
            )
        self.out["procedure_occurrence"] = out

    def _drugs(self) -> None:
        ts = self.ts
        route = {d["ndc"]: _text(d["route"]) for d in ts.rows("drug_reference")}
        rows = [
            r
            for r in ts.rows("pharmacy_claim")
            if r["member_id"] in self.person_ids and (r["claim_status"] or "").lower() == "paid"
        ]
        self.drug_ids = allocate("drug_exposure", (r["rx_claim_id"] for r in rows))
        out: list[Row] = []
        for r in rows:
            start = r["fill_date"]
            days = r["days_supply"]
            end = start + dt.timedelta(days=max((days or 1) - 1, 0))
            rt = route.get(r["ndc"])
            row = {
                "drug_exposure_id": self.drug_ids[r["rx_claim_id"]],
                "person_id": self.person_ids[r["member_id"]],
                "drug_concept_id": self.cm.get("drug", r["ndc"]),
                "drug_exposure_start_date": start,
                "drug_exposure_start_datetime": _midnight(start),
                "drug_exposure_end_date": end,
                "drug_exposure_end_datetime": _midnight(end),
                "drug_type_concept_id": TYPE_PHARMACY_CLAIM,
                "refills": r["refill_number"],
                "quantity": None if r["quantity"] is None else Decimal(str(r["quantity"])),
                "days_supply": days,
                "route_concept_id": self.cm.get("route", rt) if rt else 0,
                "provider_id": self.provider_ids.get(r["prescriber_npi"] or ""),
                "drug_source_value": r["ndc"],
                "drug_source_concept_id": 0,
                "route_source_value": rt,
            }
            out.append(row)
            self.drug[r["rx_claim_id"]] = row
        self.out["drug_exposure"] = out

    def _plan_period(self, person_id: int, plan_id: str | None, day: dt.date) -> int | None:
        spans = [
            p
            for p in self.ppp_by_person.get(person_id, [])
            if p["payer_plan_period_start_date"] <= day <= p["payer_plan_period_end_date"]
        ]
        for p in spans:
            if plan_id is None or p["_plan"] == plan_id:
                return int(p["payer_plan_period_id"])
        return None

    def _costs(self) -> None:
        ts = self.ts
        entries: list[tuple[str, str, int, Row]] = []
        if ts.has("medical_claim"):
            for c in ts.rows("medical_claim"):
                if c["claim_id"] in self.visit:
                    entries.append(
                        (
                            f"V|{c['claim_id']}",
                            "Visit",
                            self.visit[c["claim_id"]]["visit_occurrence_id"],
                            c,
                        )
                    )
        if ts.has("pharmacy_claim"):
            for r in ts.rows("pharmacy_claim"):
                if r["rx_claim_id"] in self.drug:
                    entries.append(
                        (
                            f"D|{r['rx_claim_id']}",
                            "Drug",
                            self.drug[r["rx_claim_id"]]["drug_exposure_id"],
                            r,
                        )
                    )
        ids = allocate("cost", (k for k, _, _, _ in entries))
        out: list[Row] = []
        for key, domain, event_id, src in entries:
            if domain == "Visit":
                v = self.visit[src["claim_id"]]
                patient = _sum(
                    src["member_copay"], src["member_coinsurance"], src["member_deductible"]
                )
                payer = _dec(src["total_paid"])
                row: Row = {
                    "total_charge": _dec(src["total_billed"]),
                    "total_cost": _dec(src["total_allowed"]),
                    "paid_by_payer": payer,
                    "paid_by_patient": patient,
                    "paid_patient_copay": _dec(src["member_copay"]),
                    "paid_patient_coinsurance": _dec(src["member_coinsurance"]),
                    "paid_patient_deductible": _dec(src["member_deductible"]),
                    "amount_allowed": _dec(src["total_allowed"]),
                    "drg_concept_id": 0 if src["drg_code"] else None,
                    "drg_source_value": _text(src["drg_code"]),
                }
                day, plan = v["visit_start_date"], src["plan_id"]
            else:
                p = self.drug[src["rx_claim_id"]]
                gross = _sum(src["ingredient_cost"], src["dispensing_fee"])
                payer = _dec(src["plan_paid"])
                patient = _dec(src["patient_pay"])
                row = {
                    "total_charge": gross,
                    "total_cost": gross,
                    "paid_by_payer": payer,
                    "paid_by_patient": patient,
                    "paid_ingredient_cost": _dec(src["ingredient_cost"]),
                    "paid_dispensing_fee": _dec(src["dispensing_fee"]),
                }
                v = p
                day, plan = p["drug_exposure_start_date"], src["plan_id"]
            row["total_paid"] = (
                None
                if payer is None and patient is None
                else (payer or Decimal("0.00")) + (patient or Decimal("0.00"))
            )
            out.append(
                {
                    "cost_id": ids[key],
                    "cost_event_id": event_id,
                    "cost_domain_id": domain,
                    "cost_type_concept_id": TYPE_COST,
                    "currency_concept_id": CURRENCY_USD,
                    "payer_plan_period_id": self._plan_period(v["person_id"], plan, day),
                    **row,
                }
            )
        self.out["cost"] = out


def to_arrow(name: str, rows: list[Row]) -> pa.Table:
    """The rows of one OMOP table as an Arrow table with exactly the DDL's schema."""
    schema = ddl.SCHEMAS[name]
    limits = ddl.VARCHAR_LIMITS.get(name, {})
    columns: dict[str, list[Any]] = {}
    for f in schema:
        values = [r.get(f.name) for r in rows]
        if f.name in limits:
            values = [None if v is None else str(v)[: limits[f.name]] for v in values]
        columns[f.name] = values
    return pa.table(columns, schema=schema)
