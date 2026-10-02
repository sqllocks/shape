"""HCC and RAF risk scores derived from claim diagnoses.

For each member and calendar year: the CMS-HCC categories reached by the diagnoses on paid claims
(after the hierarchies), summed into a score with an illustrative demographic factor.  The
prospective score uses the prior year's diagnoses; the concurrent score the same year's.  The
weights are the seed table's illustrative values (``reference.HCC_WEIGHT``), not the CMS published
relative factors: the score is *derived* from diagnoses, as the acceptance requires, and the
weights are replaced by the codes lane's HCC asset when it is merged."""

from __future__ import annotations

from collections.abc import Iterable

from .reference import HCC_HIERARCHY, HCC_LABEL, HCC_OF, HCC_WEIGHT

_DEMO = ((0, 0.25), (18, 0.22), (45, 0.30), (55, 0.40), (65, 0.45), (70, 0.55), (75, 0.66), (80, 0.78), (85, 0.90))


def demographic_factor(age: int, sex: str) -> float:
    value = _DEMO[0][1]
    for edge, v in _DEMO:
        if age >= edge:
            value = v
    return round(value + (0.04 if sex == "F" and 18 <= age < 65 else 0.0), 3)


def hccs_for(codes: Iterable[str]) -> list[int]:
    found = {HCC_OF[c] for c in codes if c in HCC_OF}
    for group in HCC_HIERARCHY:
        present = [h for h in group if h in found]
        for dropped in present[1:]:
            found.discard(dropped)
    return sorted(found)


def raf(age: int, sex: str, hccs: list[int]) -> float:
    return round(demographic_factor(age, sex) + sum(HCC_WEIGHT[h] for h in hccs), 4)


def label(hcc: int) -> str:
    return HCC_LABEL[hcc]
