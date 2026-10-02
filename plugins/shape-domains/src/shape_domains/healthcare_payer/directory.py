"""The provider directory: individuals, groups, facilities, labs, suppliers and pharmacies, each
with a never-assigned NPI, specialty, network status and a state-level market.  The NUCC
taxonomy code is licensed (AMA): it is filled from a bring-your-own table, not shipped."""

from __future__ import annotations

import math
from datetime import date
from typing import Any

import numpy as np

from . import identifiers
from .calibration import Calibration
from .model import Person, Provider
from .names import FEMALE, MALE, ORG_ADJ, SURNAMES
from .reference import PRIMARY_CARE, SPECIALTY

# members per provider (individuals) or per facility; minimum counts keep small runs usable
RATIOS: dict[str, int] = {
    "family_medicine": 1500,
    "internal_medicine": 2000,
    "pediatrics": 2500,
    "obgyn": 4000,
    "cardiology": 6000,
    "endocrinology": 15000,
    "nephrology": 15000,
    "pulmonology": 12000,
    "psychiatry": 8000,
    "psychology": 3000,
    "oncology": 10000,
    "radiation_oncology": 25000,
    "emergency_medicine": 6000,
    "ophthalmology": 8000,
    "optometry": 6000,
    "podiatry": 10000,
    "general_surgery": 6000,
    "orthopedics": 8000,
    "urology": 12000,
    "radiology": 5000,
    "hospitalist": 5000,
    "rheumatology": 20000,
    "dermatology": 15000,
    "gastroenterology": 12000,
    "hospital": 25000,
    "laboratory": 20000,
    "urgent_care": 15000,
    "dialysis_center": 30000,
    "ambulance": 30000,
    "dme_supplier": 20000,
    "retail_pharmacy": 3000,
}
MIN_COUNT = {"individual": 3, "organization": 2}
HIGH_PRICE = {"CA", "NY", "NJ", "MA", "WA", "DC", "CT", "AK", "HI", "MD", "IL"}
LOW_PRICE = {"AL", "AR", "MS", "KY", "WV", "OK", "SD", "ND", "ID", "NM", "LA", "IA", "KS", "NE"}


def region_of(state: str) -> str:
    return "high" if state in HIGH_PRICE else "low" if state in LOW_PRICE else "mid"


class ProviderDirectory:
    def __init__(
        self,
        state_members: dict[str, int],
        zips: dict[str, list[tuple[str, str, float, float]]],
        cal: Calibration,
        seed: int,
    ) -> None:
        self.providers: list[Provider] = []
        self._by: dict[tuple[str, str, bool], list[int]] = {}
        self.cal = cal
        self.npis: identifiers.NpiSource = identifiers.SyntheticNpis()
        rng = np.random.default_rng([seed, 7_000_001])
        net = cal.get("net.in_network_share")
        sigma = float(cal.get("price.noise_sigma"))
        groups: dict[str, list[int]] = {}
        for state, count in sorted(state_members.items()):
            zrows = zips[state]
            for spec_key, ratio in RATIOS.items():
                spec = SPECIALTY[spec_key]
                n = max(MIN_COUNT[spec.kind], math.ceil(count / ratio))
                for _ in range(n):
                    z = zrows[int(rng.integers(0, len(zrows)))]
                    if spec.kind == "individual":
                        sex_f = rng.random() < 0.45
                        pool = FEMALE if sex_f else MALE
                        name = (
                            f"{SURNAMES[int(rng.integers(0, len(SURNAMES)))]}, "
                            f"{pool[int(rng.integers(0, len(pool)))]}"
                        )
                    else:
                        adj = ORG_ADJ[int(rng.integers(0, len(ORG_ADJ)))]
                        name = f"{adj} {z[0]} {spec.name}"
                    in_net = bool(rng.random() < net["commercial"])
                    p = Provider(
                        len(self.providers),
                        self.npis.npi(len(self.providers)),
                        name,
                        spec_key,
                        "",
                        spec.kind,
                        state,
                        z[1],
                        z[0],
                        z[2],
                        z[3],
                        in_net,
                        price_tier=float(np.exp(rng.normal(0, sigma))),
                    )
                    self.providers.append(p)
                    self._by.setdefault((state, spec_key, in_net), []).append(p.idx)
                    if spec.kind == "individual":
                        groups.setdefault(state, []).append(p.idx)
            # group practices billing for individuals of the state
            ids = groups.get(state, [])
            n_groups = max(2, len(ids) // 6)
            gids = []
            for _ in range(n_groups):
                z = zrows[int(rng.integers(0, len(zrows)))]
                adj = ORG_ADJ[int(rng.integers(0, len(ORG_ADJ)))]
                g = Provider(
                    len(self.providers),
                    self.npis.npi(len(self.providers)),
                    f"{adj} {z[0]} Medical Group",
                    "group_practice",
                    "",
                    "organization",
                    state,
                    z[1],
                    z[0],
                    z[2],
                    z[3],
                    True,
                )
                self.providers.append(g)
                gids.append(g.idx)
            for i in ids:
                self.providers[i].billing_npi = self.providers[
                    gids[int(rng.integers(0, n_groups))]
                ].npi
        self.pharmacies = self._pharmacies(state_members, zips, rng)

    def _pharmacies(
        self,
        state_members: dict[str, int],
        zips: dict[str, list[tuple[str, str, float, float]]],
        rng: np.random.Generator,
    ) -> list[Provider]:
        out: list[Provider] = []
        for state, count in sorted(state_members.items()):
            for _ in range(max(3, math.ceil(count / RATIOS["retail_pharmacy"]))):
                z = zips[state][int(rng.integers(0, len(zips[state])))]
                adj = ORG_ADJ[int(rng.integers(0, len(ORG_ADJ)))]
                out.append(
                    Provider(
                        len(self.providers) + len(out),
                        self.npis.npi(len(self.providers) + len(out)),
                        f"{adj} {z[0]} Pharmacy",
                        "retail_pharmacy",
                        "",
                        "organization",
                        state,
                        z[1],
                        z[0],
                        z[2],
                        z[3],
                        True,
                    )
                )
        base = len(self.providers) + len(out)
        for i in range(3):
            st = sorted(state_members)[i % len(state_members)]
            z = zips[st][0]
            out.append(
                Provider(
                    base + i,
                    self.npis.npi(base + i),
                    f"National Mail Service Pharmacy {i + 1}",
                    "mail_pharmacy",
                    "",
                    "organization",
                    st,
                    z[1],
                    z[0],
                    z[2],
                    z[3],
                    True,
                )
            )
        # pharmacies are appended after the providers so ids stay unique and contiguous
        self.providers.extend(out)
        return out

    # ---- lookups -----------------------------------------------------------------------------
    def pick(self, person: Person, specialty: str, day: date) -> int:
        m = person.member
        if specialty in PRIMARY_CARE and m.pcp is not None and person.rng.random() < 0.85:
            if self.providers[m.pcp].specialty in (
                specialty,
                "family_medicine",
                "internal_medicine",
            ):
                return m.pcp
        return self._choose(person, m.state, specialty)

    def _choose(self, person: Person, state: str, specialty: str) -> int:
        net = self.cal.get("net.in_network_share")[person.member.lob]
        in_net = bool(person.rng.random() < net)
        for flag in (in_net, not in_net):
            ids = self._by.get((state, specialty, flag))
            if ids:
                return ids[int(person.rng.integers(0, len(ids)))]
        for (_state, sp, _net), ids in self._by.items():
            if sp == specialty and ids:
                return ids[int(person.rng.integers(0, len(ids)))]
        raise KeyError(f"no provider with specialty {specialty}")

    def pick_pharmacy(self, person: Person, mail: bool) -> Provider:
        if mail:
            mails = [p for p in self.pharmacies if p.specialty == "mail_pharmacy"]
            return mails[int(person.rng.integers(0, len(mails)))]
        local = [
            p
            for p in self.pharmacies
            if p.state == person.member.state and p.specialty == "retail_pharmacy"
        ]
        return local[int(person.rng.integers(0, len(local)))]

    def info(self, idx: int) -> Provider:
        return self.providers[idx]

    def stats(self) -> dict[str, Any]:
        return {"providers": len(self.providers)}
