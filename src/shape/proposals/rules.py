"""Contract rules as proposals: what the evidence in one or more profiles supports (W3-02).

``propose_rules`` turns profiles into contract v1 rules, each a proposal ``rule:TABLE.COLUMN.RULE``
whose ``claim`` is the exact contract fragment and whose ``evidence`` is the profile figures it
rests on. A person accepts, rejects or defers each one, and ``DecisionFile.to_contract`` writes the
accepted ones as a contract that ``shape check`` reads. The formulas, margins and the rule for
sensitive columns are in ``docs/PROPOSALS.md``; the constants below are those numbers.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from shape.contracts.v1 import check
from shape.profile.reference.profile import Profile

from ._data import DataSource
from .columns import propose_pii
from .model import PENDING, RULE, DecisionFile, Proposal, _canon

MIN_SUPPORT = 30  # fewer non-null values (or rows, for a row count) than this: no rule
MAX_ALLOWED_VALUES = 20  # allowed_values only for a column with at most this many distinct values
FD_MIN_CONFIDENCE = 0.99  # a dependency below this in any profile is not proposed
REFERENCE_MIN_RATE = 0.9  # a reference pair matching less than this is not proposed
RANGE_MARGIN = 0.10  # a range is widened by this share of its span on each side
ROW_MARGIN_ONE = 0.5  # the row count band around one profile's count
ROW_MARGIN_MANY = 0.25  # ... around the observed counts of several profiles
PII_THRESHOLD = 0.5  # a pii proposal at or above this makes its column sensitive
SUPPORT_SCALE = 10  # support n reaches half strength at n == SUPPORT_SCALE

# the most confident a rule of this kind can be: how strongly evidence implies the rule
CAPS: dict[str, float] = {
    "dtype": 0.99,
    "pattern": 0.95,
    "fd": 0.95,
    "reference_pair": 0.95,
    "nullable": 0.90,
    "unique": 0.90,
    "allowed_values": 0.85,
    "range": 0.80,
    "no_placeholder": 0.70,
    "row_count": 0.70,
}
VALUE_FREE = ("dtype", "nullable", "unique", "pattern", "no_placeholder")  # safe for personal data
_NUMERIC_TAGS = ("int", "float")
_TEMPORAL_TAGS = ("date", "timestamp")


def strength(support: int, profiles: int) -> float:
    """From 0 to 1, rising with the support (values the rule rests on) and the number of
    profiles: ``1 - (S / (support + S)) / profiles`` with ``S`` = ``SUPPORT_SCALE``."""
    return 1.0 - (SUPPORT_SCALE / (support + SUPPORT_SCALE)) / max(profiles, 1)


def confidence(rule: str, support: int, profiles: int, factor: float = 1.0) -> float:
    """The confidence of a rule: its kind's cap times :func:`strength` times ``factor`` (a
    measured rate the rule rests on, from 0 to 1)."""
    return round(min(1.0, max(0.0, CAPS[rule] * strength(support, profiles) * factor)), 4)


def rule_id(table: str, name: str) -> str:
    return f"{RULE}:{table}.{name}"


def _profile(p: Any) -> Profile:
    if isinstance(p, (str, Path)):
        import shape

        p = shape.load(str(p))
    if isinstance(p, Profile):
        return p
    if isinstance(p, dict):
        return Profile(p)
    raise ValueError(f"cannot read a profile from a {type(p).__name__}")


def _profiles(profiles: Any) -> list[Profile]:
    items = [profiles] if isinstance(profiles, (Profile, dict, str, Path)) else list(profiles)
    if not items:
        raise ValueError("give at least one profile")
    out = [_profile(p) for p in items]
    if len({p.is_dataset for p in out}) > 1:
        raise ValueError("give profiles of the same kind: all datasets or all single tables")
    return out


def _tag(value: Any) -> tuple[str, Any] | None:
    return (value[0], value[1]) if isinstance(value, list) and len(value) == 2 else None


def _sensitive(
    profiles: list[Profile],
    data: DataSource | None,
    decisions: DecisionFile | None,
    pii: Iterable[Proposal] | None,
) -> set[str]:
    """``TABLE.COLUMN`` of the columns taken for personal data: a ``pii`` proposal at or above
    ``PII_THRESHOLD`` (given, or found in the first profile by name and values), an accepted
    ``pii`` decision; not one a person rejected."""
    found = propose_pii(profiles[0], data, min_confidence=PII_THRESHOLD) if pii is None else pii
    out = {p.subject for p in found if p.confidence >= PII_THRESHOLD}
    if decisions is not None:
        for e in decisions.entries():
            if e.proposal.kind != "pii":
                continue
            subject = e.proposal.subject
            if e.status == "rejected":
                out.discard(subject)
            elif e.status == "accepted" or (
                e.status in (PENDING, "deferred") and e.proposal.confidence >= PII_THRESHOLD
            ):
                out.add(subject)
    return out


class _Run:
    def __init__(self, profiles: list[Profile]) -> None:
        self.profiles = profiles
        self.n = len(profiles)
        self.single = not profiles[0].is_dataset
        self.sensitive: set[str] = set()
        self.out: list[Proposal] = []

    def tables(self) -> dict[str, list[dict[str, Any]]]:
        """Each table of the first profile with the table of every profile (matched by name; a
        single-table profile by position), for the tables every profile has."""
        per = [p.tables for p in self.profiles]
        names = list(per[0])
        out: dict[str, list[dict[str, Any]]] = {}
        for name in names:
            if self.single:
                out[name] = [next(iter(t.values())) for t in per]
            elif all(name in t for t in per):
                out[name] = [t[name] for t in per]
        return out

    def fragment(self, table: str, body: dict[str, Any]) -> dict[str, Any]:
        return body if self.single else {"tables": {table: body}}

    def holds(self, claim: dict[str, Any]) -> bool:
        """Whether the contract fragment passes ``shape check`` on every profile."""
        return all(check(p, claim).passed for p in self.profiles)

    def add(
        self, table: str, name: str, claim: dict[str, Any], conf: float, evidence: dict[str, Any]
    ) -> None:
        self.out.append(
            Proposal(
                rule_id(table, name),
                RULE,
                f"{table}.{name}",
                claim,
                conf,
                {"profiles": self.n, **evidence},
            )
        )


def _range(cols: list[dict[str, Any]]) -> tuple[Any, Any, Any, Any] | None:
    """(low bound, high bound, observed minimum, observed maximum) widened by ``RANGE_MARGIN`` of
    the span on each side, or ``None`` when the column has no usable range."""
    mins = [_tag(c.get("min_value")) for c in cols]
    maxs = [_tag(c.get("max_value")) for c in cols]
    if any(t is None for t in (*mins, *maxs)):
        return None
    tags = {t[0] for t in (*mins, *maxs) if t is not None}
    if tags <= set(_NUMERIC_TAGS):
        lows = [t[1] for t in mins if t is not None]
        highs = [t[1] for t in maxs if t is not None]
        if not all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
            for v in (*lows, *highs)
        ):
            return None
        lo, hi = min(lows), max(highs)
        if tags == {"int"}:
            span = hi - lo
            margin = -(-span // 10) if span else max(-(-abs(lo) // 10), 1)
            return _keep_sign(lo - margin, hi + margin, lo, hi)
        span = hi - lo
        fmargin = RANGE_MARGIN * span if span else (RANGE_MARGIN * abs(lo) or 1.0)
        return _keep_sign(
            math.floor((lo - fmargin) * 1e4) / 1e4, math.ceil((hi + fmargin) * 1e4) / 1e4, lo, hi
        )
    if len(tags) == 1 and tags <= set(_TEMPORAL_TAGS):
        return _temporal_range(mins, maxs, tags.pop())
    if tags == {"str"} and all(str(c.get("dtype")) == "datetime" for c in cols):
        # a CSV date column: typed datetime, its extremes the text read from the file (#646)
        texts = [str(t[1]) for t in (*mins, *maxs) if t is not None]
        return _temporal_range(
            mins, maxs, "date" if all(len(x) == 10 for x in texts) else "timestamp"
        )
    return None


def _keep_sign(low: Any, high: Any, lo: Any, hi: Any) -> tuple[Any, Any, Any, Any]:
    """The widened bounds, but a range that was not negative (or not positive) does not cross
    zero: a negative amount is a violation, not margin."""
    if lo >= 0 and low < 0:
        low = 0 * abs(low)  # 0 or 0.0, never -0.0 (#647)
    if hi <= 0 and high > 0:
        high = 0 * high
    return low, high, lo, hi


def _temporal_range(mins: list[Any], maxs: list[Any], tag: str) -> tuple[Any, Any, Any, Any] | None:
    try:
        lows = [datetime.fromisoformat(str(t[1])) for t in mins]
        highs = [datetime.fromisoformat(str(t[1])) for t in maxs]
    except ValueError:
        return None
    if any(d.tzinfo is not None for d in (*lows, *highs)):
        return None  # a time zone does not compare as text; no range
    lo, hi = min(lows), max(highs)
    margin = max((hi - lo) * RANGE_MARGIN, timedelta(days=1))
    sample = str(next(t[1] for t in (*mins, *maxs) if t is not None))
    sep = "T" if "T" in sample else " "

    def fmt(d: datetime) -> str:
        return d.strftime("%Y-%m-%d") if tag == "date" else d.strftime(f"%Y-%m-%d{sep}%H:%M:%S")

    low = fmt(lo - margin)
    high = fmt(hi + margin + (timedelta(seconds=1) if tag != "date" else timedelta()))
    return low, high, min(str(t[1]) for t in mins if t), max(str(t[1]) for t in maxs if t)


def _allowed(cols: list[dict[str, Any]], non_null: list[int]) -> tuple[list[str], int] | None:
    """(the union of the values every profile lists, the count of the rarest value) when every
    profile lists all of its at most ``MAX_ALLOWED_VALUES`` distinct values."""
    keys: set[str] = set()
    rarest: float | None = None
    for c, n in zip(cols, non_null, strict=True):
        seen = c.get("value_counts_ext")
        card = c.get("cardinality")
        if not seen or card is None or card > MAX_ALLOWED_VALUES or len(seen) < card:
            return None
        keys |= set(seen)
        low = min(seen.values()) * n  # shares are of the non-null values
        rarest = low if rarest is None else min(rarest, low)
    if len(keys) > MAX_ALLOWED_VALUES or rarest is None:
        return None
    return sorted(keys), int(rarest)


def _typed(keys: Sequence[str]) -> list[Any]:
    """The values as the column holds them: integers, else numbers, else the text."""
    for kind in (int, float):
        try:
            vals = [kind(k) for k in keys]
        except ValueError:
            continue
        if sorted(str(v) for v in vals) == sorted(keys):
            return sorted(vals)
    return sorted(keys)


def propose_rules(
    profiles: Any,
    data: DataSource | None = None,
    *,
    min_confidence: float = 0.5,
    decisions: DecisionFile | None = None,
    pii: Iterable[Proposal] | None = None,
) -> list[Proposal]:
    """Contract v1 rules supported by every one of ``profiles`` (one profile, or several such as
    a week of daily captures), the most confident first.

    A rule is proposed only when it holds on every profile, and never from fewer than
    ``MIN_SUPPORT`` non-null values. ``decisions`` gives the sensitive columns (an accepted ``pii``
    decision) and the rules already accepted, whose claim is kept as long as it still holds on
    every profile. ``pii`` gives the personal-data proposals of this run; without it they are found
    in the first profile (and ``data``). A personal-data column gets value-free rules only.
    """
    if isinstance(min_confidence, bool) or not 0.0 <= min_confidence <= 1.0:
        raise ValueError("min_confidence must be from 0 to 1")
    run = _Run(_profiles(profiles))
    run.sensitive = _sensitive(run.profiles, data, decisions, pii)
    for table, tabs in run.tables().items():
        _row_count(run, table, tabs)
        rows = [int(t["row_count"]) for t in tabs]
        for name, cols in _columns(tabs).items():
            _column_rules(run, table, name, cols, rows)
        _joint_rules(run, table, tabs, sum(rows))
    kept = _verified(run, decisions)
    return sorted(
        (p for p in kept if p.confidence >= min_confidence), key=lambda p: (-p.confidence, p.id)
    )


def _verified(run: _Run, decisions: DecisionFile | None) -> list[Proposal]:
    """The proposals whose claim passes ``shape check`` on every profile. A rule a person already
    accepted keeps its accepted claim while that still holds; when it does not, the fresh claim is
    returned, which differs, and the decision file marks the accepted rule stale."""
    accepted = (
        {e.proposal.id: e.proposal.claim for e in decisions.accepted(RULE)} if decisions else {}
    )
    out: list[Proposal] = []
    for p in run.out:
        claim = p.claim
        old = accepted.get(p.id)
        if old is not None and old != _canon(claim) and run.holds(old):
            claim = old
        if run.holds(claim):
            out.append(Proposal(p.id, p.kind, p.subject, claim, p.confidence, p.evidence))
    return out


def _columns(tabs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    return {
        name: [t["columns"][name] for t in tabs]
        for name in tabs[0]["columns"]
        if all(name in t["columns"] for t in tabs)
    }


def _row_count(run: _Run, table: str, tabs: list[dict[str, Any]]) -> None:
    counts = [int(t["row_count"]) for t in tabs]
    total = sum(counts)
    if total < MIN_SUPPORT:
        return
    margin = ROW_MARGIN_ONE if run.n == 1 else ROW_MARGIN_MANY
    band = {
        "min": max(0, math.floor(min(counts) * (1 - margin))),
        "max": math.ceil(max(counts) * (1 + margin)),
    }
    run.add(
        table,
        "row_count",
        run.fragment(table, {"row_count": band}),
        confidence("row_count", total, run.n),
        {"rows": total, "observed_min": min(counts), "observed_max": max(counts)},
    )


def _column_rules(
    run: _Run, table: str, name: str, cols: list[dict[str, Any]], rows: list[int]
) -> None:
    null_counts = [int(c["null_count"]) for c in cols]
    non_null = [r - n for r, n in zip(rows, null_counts, strict=True)]
    nulls, support = sum(null_counts), sum(non_null)
    if support < MIN_SUPPORT:
        return
    base = {
        "rows": sum(rows),
        "nulls": nulls,
        "distinct": max(int(c["cardinality"]) for c in cols),
    }

    def put(rule: str, value: Any, conf: float, extra: dict[str, Any] | None = None) -> None:
        body = {"columns": {name: value}}
        run.add(table, f"{name}.{rule}", run.fragment(table, body), conf, {**base, **(extra or {})})

    dtypes = {c["dtype"] for c in cols}
    dtype = next(iter(dtypes))
    if len(dtypes) == 1 and dtype:
        put("dtype", {"dtype": dtype}, confidence("dtype", support, run.n))
    if nulls == 0:
        put("nullable", {"nullable": False}, confidence("nullable", support, run.n))
    tags = {(_tag(c.get("min_value")) or ("",))[0] for c in cols}
    is_unique = all(c.get("is_unique") is True for c in cols)
    if (
        is_unique
        and len(dtypes) == 1
        and (dtype == "string" or (dtype == "integer" and tags == {"int"}))
    ):
        put("unique", {"unique": True}, confidence("unique", support, run.n))
    patterns = {c.get("pattern") for c in cols}
    pattern = next(iter(patterns))
    if len(patterns) == 1 and pattern:
        rates = [float((c.get("pattern_rates") or {}).get(pattern, -1.0)) for c in cols]
        if min(rates) >= 0.0:  # every profile measured how many values match
            rate = min(rates)
            put("pattern", {"pattern": pattern}, confidence("pattern", support, run.n, rate),
                {"match_rate": rate})  # fmt: skip
    if dtype in ("string", "integer", "float") and not any(c.get("placeholders") for c in cols):
        conf = confidence("no_placeholder", support, run.n)
        put("no_placeholder", {"no_placeholder": True}, conf)
    if f"{table}.{name}" in run.sensitive or is_unique or len(dtypes) != 1:
        return  # real values (a range, a value set) are not proposed for personal data or keys
    if dtype in ("integer", "float", "datetime"):
        found = _range(cols)
        if found is not None:
            low, high, seen_lo, seen_hi = found
            put("range", {"min": low, "max": high}, confidence("range", support, run.n),
                {"observed_min": seen_lo, "observed_max": seen_hi})  # fmt: skip
    allowed = _allowed(cols, non_null) if dtype in ("string", "integer", "float") else None
    if allowed is not None:
        keys, rarest = allowed
        put("allowed_values", {"allowed_values": _typed(keys)},
            confidence("allowed_values", rarest, run.n))  # fmt: skip


def _joint_rules(run: _Run, table: str, tabs: list[dict[str, Any]], rows: int) -> None:
    joints = [t["joint"] for t in tabs if isinstance(t.get("joint"), dict)]
    if len(joints) != len(tabs):
        return
    deps = [
        {(tuple(e["determinant"]), e["dependent"]): e for e in j.get("dependencies", ())}
        for j in joints
    ]
    for key in sorted(deps[0]):
        det, dep = key
        if not all(key in d for d in deps) or rows < MIN_SUPPORT:
            continue
        confs = [d[key]["confidence"] for d in deps]
        trivial = any(
            (len(det) == 1 and t["columns"][det[0]].get("is_unique") is True)
            or t["columns"][dep].get("cardinality", 2) <= 1
            for t in tabs
        )
        if min(confs) < FD_MIN_CONFIDENCE or trivial:
            continue
        rule = {
            "determinant": det[0] if len(det) == 1 else list(det),
            "dependent": dep,
            "min_confidence": FD_MIN_CONFIDENCE,
        }
        run.add(
            table,
            f"fd.{','.join(det)}->{dep}",
            run.fragment(table, {"fd": [rule]}),
            confidence("fd", rows, run.n, min(confs)),
            {
                "rows": rows,
                "dependency_confidence": min(confs),
                "groups": max(d[key]["groups"] for d in deps),
            },
        )
    refs = [
        {(tuple(e["columns"]), e["reference"]): e for e in j.get("reference_pairs", ())}
        for j in joints
    ]
    for key in sorted(refs[0]):
        cols, reference = key
        if not all(key in r for r in refs):
            continue
        rates = [r[key]["match_rate"] for r in refs]
        if any(x is None for x in rates) or min(rates) < REFERENCE_MIN_RATE:
            continue
        counted = sum(r[key]["rows"] for r in refs)
        if counted < MIN_SUPPORT:
            continue
        floor_rate = max(0.01, math.floor((min(rates) - 0.01) * 100) / 100)
        rule = {"columns": list(cols), "reference": reference, "min_match_rate": floor_rate}
        run.add(
            table,
            f"reference_pair.{','.join(cols)}->{reference}",
            run.fragment(table, {"reference_pair": [rule]}),
            confidence("reference_pair", counted, run.n, min(rates)),
            {"rows": counted, "match_rate": min(rates), "reference": reference},
        )
