"""Plain records the simulator passes around (no Arrow here: tables are built at the end)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np


@dataclass(slots=True)
class Provider:
    idx: int
    npi: str
    name: str
    specialty: str
    taxonomy: str  # always empty: the NUCC code comes from the bring-your-own table
    kind: str  # individual | organization
    state: str
    zip: str
    city: str
    lat: float
    lon: float
    network: bool
    billing_npi: str | None = None  # the group or facility an individual bills under
    accepting: bool = True
    price_tier: float = 1.0  # contracted-rate level within the market


@dataclass(slots=True)
class Plan:
    plan_id: str
    lob: str  # commercial | ma | medicaid
    product: str  # HMO | PPO | EPO | HDHP | MA-HMO | MA-PPO | MCO
    group_id: str
    deductible: float
    oop_max: float
    coinsurance: float
    copay_pcp: float
    copay_specialist: float
    copay_ed: float
    copay_urgent: float
    inpatient_copay_day: float
    family_multiple: float = 2.0
    pcp_required: bool = False


@dataclass(slots=True)
class Span:
    start: date
    end: date | None  # None while enrolled at the end of the window
    plan_id: str
    reason: str | None = None  # termination reason


@dataclass(slots=True)
class Member:
    idx: int
    member_id: str
    subscriber_id: str
    suffix: str
    relationship: str  # 18 self, 01 spouse, 19 child (X12 INS02)
    first: str
    last: str
    sex: str
    dob: date
    lob: str
    household: int
    state: str
    zip: str
    city: str
    street: str
    lat: float
    lon: float
    ssn: str
    email: str
    phone: str
    spans: list[Span] = field(default_factory=list)
    pcp: int | None = None
    cob: bool = False
    dual: bool = False
    death: date | None = None
    frailty: float = 1.0
    group_id: str = ""

    def age(self, day: date) -> int:
        return day.year - self.dob.year - ((day.month, day.day) < (self.dob.month, self.dob.day))

    def span_on(self, day: date) -> Span | None:
        for s in self.spans:
            if s.start <= day and (s.end is None or day <= s.end):
                return s
        return None


@dataclass(slots=True)
class Cond:
    """A condition on the problem list."""

    key: str
    onset: date
    code: str = ""  # ICD-10-CM code or concept (resolved on the date of service)
    stage: int = 0
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Therapy:
    """A maintenance or acute course of one drug."""

    drug_key: str
    start: date
    stop: date | None
    indication: str
    dx: str
    specialty: str
    days_supply: int
    refills: int
    daw: str = "0"
    acute: bool = False
    encounter_id: int | None = None
    quantity: float | None = None


@dataclass(slots=True)
class Svc:
    """One billed service within an encounter."""

    key: str
    units: int = 1
    dx_ptr: tuple[int, ...] = (1,)
    modifiers: tuple[str, ...] = ()
    day_offset: int = 0
    specialty: str | None = None  # a different rendering specialty than the encounter's


@dataclass(slots=True)
class Encounter:
    eid: int
    member: int
    day: date
    # office | telehealth | urgent | ed | inpatient | outpatient_hospital | lab | imaging | asc
    # | dialysis | transport | dme
    setting: str
    specialty: str
    dx: list[tuple[str, str]]  # (ICD-10-CM, present-on-admission flag or "")
    services: list[Svc]
    module: str
    reason: str
    admit: date | None = None
    discharge: date | None = None
    drg: str | None = None
    pcs: list[str] = field(default_factory=list)
    status_code: str = "01"  # patient discharge status (01 home, 20 expired, 03 SNF)
    via_ed: bool = False
    emergency: bool = False
    provider: int | None = None
    facility: int | None = None
    prior_auth: bool = False
    readmit_of: int | None = None
    parent_stay: int | None = None  # a professional/ancillary encounter that belongs to a stay
    code_edits: int = 0


@dataclass(slots=True)
class Person:
    member: Member
    rng: np.random.Generator
    conds: dict[str, Cond] = field(default_factory=dict)
    therapies: list[Therapy] = field(default_factory=list)
    encounters: list[Encounter] = field(default_factory=list)
    last_done: dict[str, date] = field(default_factory=dict)
    flags: dict[str, Any] = field(default_factory=dict)
    adherence: float = 0.8

    def has(self, key: str) -> bool:
        return key in self.conds

    def age(self, day: date) -> int:
        return self.member.age(day)
