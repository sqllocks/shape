"""Component-level fingerprints for pooled text (T-21 (e): "pooled high-cardinality strings have
vocabulary overlap >= 0.999 against the baseline's output plus its pools, or parse at component
level into pool members").

A value such as ``moor.willibrook332@tutanota.com`` is parsed with a regular expression into
named components; each component is either a member of a pool (checked against the pool, and
counted in 50 equal slices of the pool so the draw can be compared with the baseline's) or a
number (fingerprinted like any numeric column). Standard library only, plus ``fingerprint`` and
``compare`` of this directory, so the baseline venv and the Shape venv build the same documents.
"""

from __future__ import annotations

import itertools
import math
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import compare
import fingerprint

BUCKETS = 50


def slug(text: str) -> str:
    """Lower case without spaces (a name inside an e-mail address)."""
    return text.lower().replace(" ", "")


def company_stem(text: str) -> str:
    """A company name as the baseline writes it into an address."""
    return text.lower().replace(" ", "").replace(",", "").replace(".", "")[:20]


TRANSFORMS: dict[str, Callable[[str], str]] = {
    "none": lambda s: s,
    "slug": slug,
    "company_stem": company_stem,
}


def _index(entries: Sequence[str], transform: str) -> dict[str, int]:
    out: dict[str, int] = {}
    fn = TRANSFORMS[transform]
    for i, e in enumerate(entries):
        out.setdefault(fn(e), i)
    return out


def parts_fingerprint(
    values: Sequence[Any], spec: Mapping[str, Any], pools: Mapping[str, Sequence[str]]
) -> dict[str, Any]:
    """``spec`` is ``{"regex": ..., "groups": {name: kind}}``; a kind is ``["pool", pool,
    transform]``, ``["chars", alphabet]`` or ``["int"]``."""
    rx = re.compile(spec["regex"])
    present = [v for v in values if v is not None]
    columns: dict[str, list[str]] = {g: [] for g in spec["groups"]}
    matched = 0
    for v in present:
        m = rx.fullmatch(v)
        if m is None:
            continue
        matched += 1
        for g in columns:
            columns[g].append(m.group(g))
    out: dict[str, Any] = {"parsed": matched / max(1, len(present)), "groups": {}}
    for g, kind in spec["groups"].items():
        raw = columns[g]
        if kind[0] == "pool":
            entries = pools[kind[1]]
            index = _index(entries, kind[2])
            hits = [index.get(x) for x in raw]
            counted = Counter(h * BUCKETS // len(entries) for h in hits if h is not None)
            out["groups"][g] = {
                "kind": "pool",
                "n": len(raw),
                "in_pool": sum(h is not None for h in hits) / max(1, len(raw)),
                "counts": {str(k): v for k, v in sorted(counted.items())},
            }
        elif kind[0] == "chars":
            counted_chars = Counter(c for x in raw for c in x)
            total = sum(counted_chars.values())
            out["groups"][g] = {
                "kind": "chars",
                "n": len(raw),
                "in_alphabet": sum(v for c, v in counted_chars.items() if c in kind[1])
                / max(1, total),
                "counts": dict(sorted(counted_chars.items())),
            }
        else:
            out["groups"][g] = fingerprint.fingerprint([int(x) for x in raw])
    return out


def check_parts(shape: Mapping[str, Any], baseline: list[dict[str, Any]]) -> list[str]:
    """The failures of ``shape`` against the baseline's component fingerprints."""
    fails: list[str] = []
    if shape["parsed"] < 1.0:
        fails.append(f"only {shape['parsed']:.5f} of the values parse into their components")
    for g, got in shape["groups"].items():
        refs = [b["groups"][g] for b in baseline]
        if got.get("kind") == "chars":
            if got["in_alphabet"] < 1.0:
                fails.append(
                    f"{g}: {1 - got['in_alphabet']:.5f} of the characters leave the alphabet"
                )
            fails += [
                f"{g}: {f}"
                for f in compare._categorical(
                    {"counts": got["counts"]}, [{"counts": r["counts"]} for r in refs], "character"
                )
            ]
        elif got.get("kind") == "pool":
            if got["in_pool"] < 0.999:
                fails.append(f"{g}: pool membership {got['in_pool']:.5f} < 0.999")
            fails += [
                f"{g}: {f}"
                for f in compare._categorical(
                    {"counts": got["counts"]}, [{"counts": r["counts"]} for r in refs], "pool slice"
                )
            ]
        else:
            fails += [f"{g}: {f}" for f in compare.check(got, refs)]
    return fails


def _days_between(later: Sequence[Any], earlier: Sequence[Any]) -> list[int | None]:
    """Whole days from ``earlier`` to ``later`` (``None`` where either is missing)."""
    import numpy as np

    def ns(values: Sequence[Any]) -> Any:
        clean = [None if (v is None or v != v) else v for v in values]
        return np.array([np.datetime64("NaT") if v is None else v for v in clean],
                        dtype="datetime64[ns]")  # fmt: skip

    delta = (ns(later) - ns(earlier)) / np.timedelta64(1, "D")
    return [None if x != x else int(round(x)) for x in delta.tolist()]


def measure(
    case: Mapping[str, Any], tables: Mapping[str, Mapping[str, Sequence[Any]]]
) -> list[Any]:
    """The series a case compares: its target column, or the day offset it adds to a date."""
    table, column = case["target"]
    values = list(tables[table][column])
    how = case["measure"]
    if how["kind"] == "values":
        return values
    if how["kind"] == "day_offset":
        return _days_between(values, tables[table][how["source"]])
    if how["kind"] == "cross_day_offset":
        ptable, pcolumn = how["parent"]
        pkey = case["tables"][ptable]["primary_key"][0]
        lookup = dict(zip(tables[ptable][pkey], tables[ptable][pcolumn], strict=True))
        sources = [lookup.get(k, None) for k in tables[table][how["via"]]]
        return _days_between(values, sources)
    raise ValueError(f"unknown measure {how['kind']!r}")


def check_series(shape: Mapping[str, Any], baseline: list[dict[str, Any]]) -> list[str]:
    """T-21 clauses (b), (c) and (d) for one measured series, and nothing else: the null rate;
    KS for numbers and datetimes; total variation distance with vocabulary overlap for columns
    with few distinct values; the length distribution for text (what it is made of is judged by
    the component check, ``check_parts``). Tolerances are ``compare``'s, which are the plan's."""
    base0 = baseline[0]
    if shape["kind"] != base0["kind"]:
        return [f"kind {shape['kind']} != {base0['kind']}"]
    fails: list[str] = []
    if shape.get("dtype") != base0.get("dtype"):
        fails.append(f"dtype {shape.get('dtype')} != {base0.get('dtype')}")
    fails += compare._null_rate(shape, baseline)
    if "counts" in shape and all("counts" in b for b in baseline):
        return fails + compare._categorical(shape, baseline, "value")
    if shape["kind"] in ("numeric", "datetime"):
        qs = [b["quantiles"] for b in baseline]
        drift = max((compare.ks_grids(a, b) for a, b in itertools.combinations(qs, 2)), default=0.0)
        n_base = sum(b["n"] - b["null_count"] for b in baseline) / len(baseline)
        n_shape = shape["n"] - shape["null_count"]
        crit = compare.CRIT_001 * math.sqrt(1.0 / n_shape + 1.0 / n_base)
        tol = max(crit, 1.5 * drift + 0.002)
        got = max(compare.ks_grids(shape["quantiles"], q) for q in qs)
        if got > tol:
            fails.append(f"KS {got:.5f} > {tol:.5f}")
    elif shape["kind"] == "string":
        fails += compare._categorical(
            {"counts": shape["lengths"]}, [{"counts": b["lengths"]} for b in baseline], "length"
        )
    return fails
