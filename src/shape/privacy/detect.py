"""Conservative local semantic detectors for common sensitive identifiers."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Detection:
    kind: str
    confidence: float
    reason: str


_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SSN = re.compile(r"^\d{3}-?\d{2}-?\d{4}$")
_PHONE = re.compile(r"^\+?[\d(). -]{7,20}$")
_IPV4 = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")
# Dates match the phone syntax (8 digits and separators); they are not phone numbers (#400).
_DATE = re.compile(r"^(?:\d{4}([-/.])\d{1,2}\1\d{1,2}|\d{1,2}([-/.])\d{1,2}\2\d{2,4})$")


def detect_value(value: Any) -> tuple[Detection, ...]:
    if value is None:
        return ()
    s = str(value).strip()
    out: list[Detection] = []
    if _EMAIL.match(s):
        out.append(Detection("email", 0.98, "email syntax"))
    if _SSN.match(s):
        out.append(Detection("us_ssn", 0.95, "SSN syntax"))
    if _PHONE.match(s) and sum(c.isdigit() for c in s) >= 7 and not _DATE.match(s):
        out.append(Detection("phone", 0.75, "telephone-like syntax"))
    if _IPV4.match(s):
        try:
            if all(0 <= int(x) <= 255 for x in s.split(".")):
                out.append(Detection("ipv4", 0.98, "IPv4 syntax"))
        except ValueError:
            pass
    return tuple(out)


def detect_column(values: Iterable[Any], sample_limit: int = 1000) -> tuple[Detection, ...]:
    counts: dict[str, int] = {}
    n = 0
    for v in values:
        if n >= sample_limit:
            break
        if v is None:
            continue
        n += 1
        for d in detect_value(v):
            counts[d.kind] = counts.get(d.kind, 0) + 1
    if not n:
        return ()
    return tuple(
        Detection(k, c / n, f"{c}/{n} sampled non-null values matched")
        for k, c in sorted(counts.items())
        if c / n >= 0.2
    )
