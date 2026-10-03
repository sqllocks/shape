"""The lint report for a design input: grain declared and sufficient, additivity declared,
foreign-key coverage, naming.

Every finding has a stable ``code``, a ``severity`` (``error``, ``warning`` or ``info``), the
``path`` of the thing it is about and a message. The report is sorted by severity, code and
path, so the same input gives the same report.

====== ======== =============================================================================
Code   Severity Meaning
====== ======== =============================================================================
D001   error    a fact has no declared grain
D002   error    the grain does not determine an attribute the fact uses
D003   error    a grain attribute has no column in the fact (not a dimension, date or
                degenerate dimension)
M001   warning  a measure has no declared additivity
M002   warning  a semi-additive measure does not say which dimension it is not additive over
M003   warning  an additive measure is not numeric
F001   warning  a reference attribute of the fact's source is neither a dimension nor degenerate
F002   error    a dimension is reached through an attribute that references another entity
K001   warning  an entity declares no key
N001   warning  a name is not a plain identifier (letters, digits and underscores)
N002   error    a table or column name is longer than 63 characters (the PostgreSQL limit)
E001   info     an entity no fact uses (dimensional modes)
G001   error    the schema cannot be derived (the message says why)
====== ======== =============================================================================
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from shape.design.engine import MODES, derive, entity_fds
from shape.design.fd import closure
from shape.design.model import DesignError, DesignInput
from shape.generation.ddl_names import snake

MAX_IDENTIFIER = 63
_NUMERIC = ("integer", "decimal", "float")
_SEVERITY = {"error": 0, "warning": 1, "info": 2}
_IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class Finding:
    code: str
    severity: str
    path: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "path": self.path,
            "message": self.message,
        }


def lint(design: DesignInput, mode: str = "star") -> list[Finding]:
    """Every finding for ``design`` in ``mode`` (``3nf``, ``star`` or ``snowflake``)."""
    if mode not in MODES:
        raise DesignError(f"unknown mode {mode!r}; choose one of {', '.join(MODES)}")
    out: list[Finding] = []
    _names(design, out)
    _keys(design, out)
    for f in design.facts:
        _fact(design, f.name, out)
    if mode != "3nf":
        _unused(design, out)
    try:
        result = derive(design, mode)
    except DesignError as exc:
        # D001 to D003 already explain the fact problems; do not repeat them as G001.
        if not any(f.code in ("D001", "D002", "D003") for f in out):
            out.append(Finding("G001", "error", "design", str(exc)))
    else:
        for t in result.tables:
            for name in (t.name, *(c.name for c in t.columns)):
                if len(name) > MAX_IDENTIFIER:
                    out.append(
                        Finding(
                            "N002",
                            "error",
                            f"tables.{t.name}",
                            f"{name!r} is {len(name)} characters; the limit is {MAX_IDENTIFIER}",
                        )
                    )
    out = sorted(set(out), key=lambda f: (_SEVERITY[f.severity], f.code, f.path, f.message))
    return out


def _names(design: DesignInput, out: list[Finding]) -> None:
    def check(name: str, path: str) -> None:
        if not _IDENT.match(name):
            out.append(Finding("N001", "warning", path, f"{name!r} is not a plain identifier"))

    for e in design.entities:
        check(e.name, f"entities.{e.name}")
        for a in e.attributes:
            check(a.name, f"entities.{e.name}.attributes.{a.name}")
    for f in design.facts:
        check(f.name, f"facts.{f.name}")
        for m in f.measures:
            check(m.name, f"facts.{f.name}.measures.{m.name}")
    if len({snake(e.name) for e in design.entities}) != len(design.entities):
        out.append(Finding("N001", "warning", "entities", "two entity names give the same table"))


def _keys(design: DesignInput, out: list[Finding]) -> None:
    for e in design.entities:
        if not e.keys:
            out.append(
                Finding(
                    "K001",
                    "warning",
                    f"entities.{e.name}",
                    f"entity {e.name!r} declares no key; one is derived from its dependencies",
                )
            )


def _fact(design: DesignInput, name: str, out: list[Finding]) -> None:
    f = next(x for x in design.facts if x.name == name)
    path = f"facts.{f.name}"
    src = design.entity(f.source)
    if not f.grain:
        out.append(Finding("D001", "error", path, f"fact {f.name!r} has no declared grain"))
    else:
        determined = closure(f.grain, entity_fds(src))
        used = (
            [m.attribute for m in f.measures]
            + [d.via for d in f.dimensions]
            + list(f.dates)
            + list(f.junk)
        )
        for a in dict.fromkeys(used):
            if a not in determined:
                out.append(
                    Finding(
                        "D002",
                        "error",
                        path,
                        f"the grain ({', '.join(f.grain)}) does not determine {a!r}",
                    )
                )
        mapped = {d.via for d in f.dimensions} | set(f.dates) | set(f.degenerate)
        for a in f.grain:
            if a not in mapped:
                out.append(
                    Finding(
                        "D003",
                        "error",
                        path,
                        f"grain attribute {a!r} is not a dimension reference, a date or a "
                        f"degenerate dimension",
                    )
                )
    for m in f.measures:
        mpath = f"{path}.measures.{m.name}"
        if m.additivity is None:
            out.append(Finding("M001", "warning", mpath, f"measure {m.name!r} has no additivity"))
        if m.additivity == "semi_additive" and not m.not_additive_over:
            out.append(
                Finding(
                    "M002",
                    "warning",
                    mpath,
                    f"semi-additive measure {m.name!r} does not list not_additive_over",
                )
            )
        if m.additivity == "additive" and src.attribute(m.attribute).type not in _NUMERIC:
            out.append(
                Finding(
                    "M003",
                    "warning",
                    mpath,
                    f"additive measure {m.name!r} is a {src.attribute(m.attribute).type}, "
                    f"not a number",
                )
            )
    covered = {d.via for d in f.dimensions} | set(f.degenerate) | set(f.dates)
    for attr in src.attributes:
        if attr.references is not None and attr.name not in covered:
            out.append(
                Finding(
                    "F001",
                    "warning",
                    f"{path}.source.{attr.name}",
                    f"{attr.name!r} references {attr.references!r} but is neither a dimension of "
                    f"fact {f.name!r} nor degenerate",
                )
            )
    for d in f.dimensions:
        ref = src.attribute(d.via).references
        if ref is not None and ref != d.entity:
            out.append(
                Finding(
                    "F002",
                    "error",
                    f"{path}.dimensions.{d.entity}",
                    f"{d.via!r} references {ref!r}, not {d.entity!r}",
                )
            )


def _unused(design: DesignInput, out: list[Finding]) -> None:
    if not design.facts:
        return
    used = {f.source for f in design.facts}
    for f in design.facts:
        used |= {d.entity for d in f.dimensions} | set(f.many_to_many)
    for e in design.entities:
        if e.name not in used:
            out.append(
                Finding("E001", "info", f"entities.{e.name}", f"no fact uses entity {e.name!r}")
            )
