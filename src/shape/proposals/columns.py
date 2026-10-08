"""PII classification and column semantics as proposals, from names, profile patterns and values.

Both follow the same pattern as relationships: Shape proposes with evidence and a confidence, a
person decides, and the decision is kept. One proposal per column and kind, ``pii:TABLE.COLUMN``
and ``semantic:TABLE.COLUMN``, holding the best candidate.
"""

from __future__ import annotations

import re
from typing import Any

from shape.privacy.detect import detect_value

from ._data import DataSource, dataset_of, load_data
from .model import Proposal

SAMPLE_LIMIT = 1000
_NAME_ONLY = 0.6  # confidence of a hint from the column name alone
_WEAK_NAME = 0.4  # a bare "name": too ambiguous to propose unless asked for


def _norm(name: str) -> str:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


# (pattern on the normalised name, what it is, confidence from the name alone)
_PII_NAMES: tuple[tuple[str, str, float], ...] = (
    (r"(^|_)e_?mail($|_)", "email", _NAME_ONLY),
    (r"(^|_)(ssn|social_security(_number)?)($|_)", "us_ssn", _NAME_ONLY),
    (r"(^|_)(phone|mobile|telephone|tel|fax)($|_)", "phone", _NAME_ONLY),
    (r"(^|_)ip(_?address)?($|_)", "ipv4", _NAME_ONLY),
    (r"(^|_)(dob|birth_?date|date_of_birth|birthday)($|_)", "date_of_birth", _NAME_ONLY),
    (r"(^|_)(credit_?card|card_?number|pan)($|_)", "payment_card", _NAME_ONLY),
    (r"(^|_)passport(_?(no|number))?($|_)", "passport", _NAME_ONLY),
    (r"^(first|last|full|middle|sur|given|family)_?name$|^surname$", "person_name", _NAME_ONLY),
    (
        r"(^|_)(street|home|billing|shipping|postal|mailing)_?address($|_)|^address$",
        "postal_address",
        _NAME_ONLY,
    ),
    (r"^name$", "person_name", _WEAK_NAME),
)

_SEMANTIC_NAMES: tuple[tuple[str, str, float], ...] = (
    (r"(^|_)e_?mail($|_)", "email", 0.7),
    (r"(^|_)(phone|mobile|telephone|tel|fax)($|_)", "phone", 0.7),
    (r"^(first|given)_?name$", "first_name", 0.7),
    (r"^(last|sur|family)_?name$|^surname$", "last_name", 0.7),
    (r"^(full_?name|name)$", "person_name", 0.55),
    (r"(^|_)(zip|postal)(_?code)?($|_)", "postal_code", 0.7),
    (r"(^|_)city($|_)", "city", 0.7),
    (r"(^|_)country(_?(code|name))?($|_)", "country", 0.7),
    (r"(^|_)(state|province|region)($|_)", "region", 0.55),
    (r"(^|_)(street|address)($|_)", "street_address", 0.55),
    (r"(^|_)(url|website|link)($|_)", "url", 0.7),
    (r"(^|_)(lat|latitude)$", "latitude", 0.7),
    (r"(^|_)(lon|lng|longitude)$", "longitude", 0.7),
    (r"(^|_)(dob|birth_?date|date_of_birth)($|_)", "date_of_birth", 0.7),
    (r"(^|_)currency(_?code)?$", "currency_code", 0.7),
)
_PATTERN_SEMANTICS = {"email": "email", "uuid": "uuid"}


def _name_hint(table: tuple[tuple[str, str, float], ...], column: str) -> tuple[str, float] | None:
    n = _norm(column)
    for pattern, kind, conf in table:
        if re.search(pattern, n):
            return kind, conf
    return None


def _sampled_detections(values: Any) -> tuple[int, dict[str, tuple[int, float]]]:
    """(values sampled, {detector: (matches, detector confidence)}) for the detectors that matched
    at least a fifth of them."""
    sampled = 0
    counts: dict[str, int] = {}
    confs: dict[str, float] = {}
    for v in values:
        if sampled >= SAMPLE_LIMIT:
            break
        if v is None:
            continue
        sampled += 1
        for d in detect_value(v):
            counts[d.kind] = counts.get(d.kind, 0) + 1
            confs[d.kind] = d.confidence
    return sampled, {k: (c, confs[k]) for k, c in counts.items() if c / sampled >= 0.2}


def propose_pii(
    profile: Any, data: DataSource | None = None, *, min_confidence: float = 0.5
) -> list[Proposal]:
    """Columns that probably hold personal data, with the name hint and, given ``data``, the
    share of sampled values that match a detector."""
    _check(min_confidence)
    ds = dataset_of(profile)
    tables = load_data(data, ds)
    out: list[Proposal] = []
    for tname, tp in ds.tables.items():
        for cname, col in tp.columns.items():
            hint = _name_hint(_PII_NAMES, cname)
            best: tuple[float, str, dict[str, Any]] | None = None
            evidence: dict[str, Any] = {}
            if hint:
                evidence["name"] = {"hint": hint[0], "confidence": hint[1]}
                best = (hint[1], hint[0], evidence)
            if tname in tables and col.dtype == "string":
                sampled, found = _sampled_detections(
                    tables[tname][cname].slice(0, SAMPLE_LIMIT * 4).to_pylist()
                )
                for kind in sorted(found):
                    matched, detector_conf = found[kind]
                    conf = matched / sampled * detector_conf
                    if hint and hint[0] == kind:
                        conf = min(1.0, conf + 0.1)
                    if best is None or conf > best[0]:
                        evidence = {
                            **({"name": {"hint": hint[0], "confidence": hint[1]}} if hint else {}),
                            "values": {"detector": kind, "matched": matched, "sampled": sampled},
                        }
                        best = (conf, kind, evidence)
            if best is not None and best[0] >= min_confidence:
                out.append(
                    Proposal(
                        f"pii:{tname}.{cname}", "pii", f"{tname}.{cname}",
                        {"pii": best[1]}, best[0], best[2],
                    )
                )  # fmt: skip
    return sorted(out, key=lambda p: (-p.confidence, p.id))


def propose_semantics(
    profile: Any, data: DataSource | None = None, *, min_confidence: float = 0.5
) -> list[Proposal]:
    """What each column probably means (email, city, postal code ...), from its name and the
    pattern the profiler found in its values. ``data`` is accepted so the three proposal
    functions share a signature; semantics need nothing the profile does not hold."""
    _check(min_confidence)
    ds = dataset_of(profile)
    load_data(data, ds)  # validates the data against the profile
    out: list[Proposal] = []
    for tname, tp in ds.tables.items():
        for cname, col in tp.columns.items():
            hint = _name_hint(_SEMANTIC_NAMES, cname)
            pattern = _PATTERN_SEMANTICS.get(col.pattern or "")
            if hint is None and pattern is None:
                continue
            evidence: dict[str, Any] = {}
            if hint:
                evidence["name"] = {"hint": hint[0], "confidence": hint[1]}
            if pattern:
                evidence["pattern"] = {"profile": col.pattern}
            if hint and pattern and hint[0] == pattern:
                kind, conf = hint[0], min(1.0, hint[1] + 0.25)
            elif pattern:
                kind, conf = pattern, 0.8
            else:
                assert hint is not None
                kind, conf = hint
            if conf >= min_confidence:
                out.append(
                    Proposal(
                        f"semantic:{tname}.{cname}", "semantic", f"{tname}.{cname}",
                        {"semantic": kind}, conf, evidence,
                    )
                )  # fmt: skip
    return sorted(out, key=lambda p: (-p.confidence, p.id))


def _check(min_confidence: float) -> None:
    if isinstance(min_confidence, bool) or not 0.0 <= min_confidence <= 1.0:
        raise ValueError("min_confidence must be from 0 to 1")
