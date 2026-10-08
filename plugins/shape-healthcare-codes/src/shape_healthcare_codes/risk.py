"""A reference risk score from the published CMS-HCC, ESRD and RxHCC tables.

:func:`score` applies the three code-set tables of this plugin to one person: the ICD-10-CM to
category mapping (``hcc``), the hierarchies (``hcc_hierarchy``) and the coefficients
(``hcc_coefficients``). It is a documented reference for testing generated data (for example,
that a generator's risk scores follow the published tables), **not** a certified implementation
of the CMS software, and makes no claim of equivalence with it. What it does not do:

* it does not derive demographic cells or interactions: the caller names them in
  ``demographics`` (``{"F70_74": 1, "DIABETES_HF_V28": 1}``), using the published variable names
  of the segment;
* it applies no diagnosis edits (age and sex), no normalization factor, no coding-intensity
  adjustment and no MA adjustment: the score is the sum of the published relative factors;
* it does not apply the ESRD transplant and graft adjustment factors that have no segment.

The steps:

1. every code is normalized (``E11.65`` -> ``E1165``) and mapped to the model's categories; a
   code with no category contributes nothing. With ``payment_year``, a mapping row counts only
   when its ``payment_years`` include that year, when the mapping carries payment-year flags for
   that year and model at all (the 2027 initial mapping has flags for 2026 only, so for 2027
   every row of the model counts);
2. the hierarchies are applied in the published order, as the CMS software does: a rule fires
   when its category is still present, and removes the categories it drops;
3. each remaining category with a coefficient in the segment (``HCC<n>`` or ``RXHCC<n>``) adds
   it; a segment with count variables (``D1`` ... ``D10P``) adds the one matching the number of
   those categories (``D<n>P`` is "n or more");
4. each variable named in ``demographics`` adds its coefficient.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape_healthcare_codes.model import normalize_code
from shape_healthcare_codes.store import read_table

_CAT = re.compile(r"^(?:RX)?HCC(\d+)$")
_COUNT = re.compile(r"^D(\d+)(P?)$")


def _num(hcc: str) -> tuple[int, str]:
    return (int(hcc), hcc) if hcc.isdigit() else (1 << 30, hcc)


def _allowed(kind: str, value: object, allowed: Iterable[Any]) -> ValueError:
    shown = ", ".join(str(a) for a in sorted(set(allowed), key=str))
    return ValueError(f"unknown {kind} {value!r}; allowed: {shown}")


def mapping_index(table: pa.Table, model: str, payment_year: int | None) -> dict[str, set[str]]:
    """Code -> categories of ``model`` in the ``hcc`` mapping table (step 1 of :func:`score`)."""
    rows = [r for r in table.to_pylist() if r["model"] == model]
    flagged = payment_year is not None and any(
        payment_year in (r["payment_years"] or []) for r in rows
    )
    out: dict[str, set[str]] = {}
    for r in rows:
        if flagged and payment_year not in (r["payment_years"] or []):
            continue
        out.setdefault(r["code"], set()).add(str(r["hcc"]))
    return out


def apply_hierarchy(
    categories: Iterable[str], rules: Iterable[tuple[str, Iterable[str]]]
) -> tuple[list[str], list[str]]:
    """``(kept, dropped)`` after applying ``rules`` (``(hcc, drops)`` in the published order)."""
    present = set(categories)
    dropped: set[str] = set()
    for hcc, drops in rules:
        if hcc not in present:
            continue
        for d in drops:
            if d in present:
                present.discard(d)
                dropped.add(d)
    return sorted(present, key=_num), sorted(dropped, key=_num)


def _flags(demographics: Mapping[str, Any] | Iterable[str] | None) -> list[str]:
    if demographics is None:
        return []
    if isinstance(demographics, str):
        raise ValueError("demographics is a mapping of variable to 0 or 1, or a list of names")
    if isinstance(demographics, Mapping):
        out = []
        for k, v in demographics.items():
            if isinstance(v, bool) or v in (0, 1):
                if v:
                    out.append(str(k))
            else:
                raise ValueError(f"demographics[{k!r}] is {v!r}; a variable is 0 or 1")
        return list(dict.fromkeys(out))
    return list(dict.fromkeys(str(k) for k in demographics))


def score(
    codes: Iterable[str],
    model: str,
    segment: str,
    demographics: Mapping[str, Any] | Iterable[str] | None = None,
    payment_year: int | None = None,
    *,
    software: str | None = None,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    """Score one person: ``{"categories", "dropped", "terms", "score"}``.

    ``categories`` are the category numbers after the hierarchies, ``dropped`` the ones the
    hierarchies removed, ``terms`` the ``{"variable", "coefficient"}`` pairs added (coefficients
    as :class:`~decimal.Decimal`, exactly as published) and ``score`` their sum. ``software``
    picks the package when several carry the model and segment (RxHCC V08: ``R0826.84.T2``,
    ``.Y1``, ``.Y2``). Unknown ``model``, ``segment``, ``software``, ``payment_year`` or
    demographic variable raises ``ValueError`` naming the allowed values.
    """
    coef = read_table("hcc_coefficients", data_dir).to_pylist()
    models = {r["model"] for r in coef}
    if model not in models:
        raise _allowed("model", model, models)
    rows = [r for r in coef if r["model"] == model]
    segments = {r["segment"] for r in rows if r["segment"] is not None}
    if segment not in segments:
        raise _allowed(f"segment for {model}", segment, segments)
    rows = [r for r in rows if r["segment"] == segment]
    if payment_year is not None:
        years = {y for r in rows for y in r["payment_years"] or []}
        if payment_year not in years:
            raise _allowed(f"payment_year for {model} {segment}", payment_year, years)
        rows = [r for r in rows if payment_year in (r["payment_years"] or [])]
    packages = {r["software"] for r in rows}
    if software is not None:
        if software not in packages:
            raise _allowed(f"software for {model} {segment}", software, packages)
        rows = [r for r in rows if r["software"] == software]
    elif len(packages) > 1:
        raise ValueError(
            f"{model} {segment} is published by several packages; pass software= one of: "
            f"{', '.join(sorted(packages))}"
        )
    values = {r["variable"]: Decimal(r["coefficient"]) for r in rows}
    cat_var = {m.group(1): v for v in values if (m := _CAT.match(v))}
    counts = {v: (int(m.group(1)), m.group(2) == "P") for v in values if (m := _COUNT.match(v))}

    flags = _flags(demographics)
    for name in flags:
        if name in cat_var.values() or name in counts:
            raise ValueError(f"{name!r} is derived from the codes; do not pass it in demographics")
        if name not in values:
            others = [v for v in values if v not in cat_var.values() and v not in counts]
            raise _allowed(f"variable for {model} {segment}", name, others)

    mapping = mapping_index(read_table("hcc", data_dir), model, payment_year)
    found = {h for c in codes for h in mapping.get(normalize_code(str(c)), ())}
    rules = [
        (r["hcc"], r["drops"] or [])
        for r in read_table("hcc_hierarchy", data_dir).to_pylist()
        if r["model"] == model
    ]
    kept, dropped = apply_hierarchy(found, rules)

    terms: list[dict[str, Any]] = []
    paid = [cat_var[h] for h in kept if h in cat_var]
    terms.extend({"variable": v, "coefficient": values[v]} for v in paid)
    n = len(paid)
    for v, (k, plus) in sorted(counts.items(), key=lambda kv: kv[1]):
        if (plus and n >= k) or (not plus and n == k):
            terms.append({"variable": v, "coefficient": values[v]})
    terms.extend({"variable": v, "coefficient": values[v]} for v in flags)
    total = sum((t["coefficient"] for t in terms), Decimal(0))
    return {"categories": kept, "dropped": dropped, "terms": terms, "score": total}


def table_problems(mapping: pa.Table, hierarchy: pa.Table, coefficients: pa.Table) -> list[str]:
    """What does not join: every ``hcc`` of the hierarchy table and every category variable
    (``HCC<n>``, ``RXHCC<n>``) of the coefficient table must be a category of the same model in
    the mapping table. Returns one message per problem (empty when the tables agree)."""
    known: dict[str, set[str]] = {}
    for r in mapping.select(["model", "hcc"]).to_pylist():
        known.setdefault(r["model"], set()).add(str(r["hcc"]))
    out: list[str] = []
    for r in hierarchy.select(["model", "hcc"]).to_pylist():
        if r["hcc"] not in known.get(r["model"], set()):
            out.append(f"hcc_hierarchy: {r['model']} category {r['hcc']} is not in the mapping")
    seen: set[tuple[str, str]] = set()
    for r in coefficients.select(["model", "variable"]).to_pylist():
        m = _CAT.match(r["variable"])
        key = (r["model"], r["variable"])
        if m and key not in seen and m.group(1) not in known.get(r["model"], set()):
            seen.add(key)
            out.append(
                f"hcc_coefficients: {r['model']} variable {r['variable']} is not in the mapping"
            )
    return out
