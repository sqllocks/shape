"""Change classes: every drift kind is breaking, additive or cosmetic, and a set of changes is a
version bump (``docs/DRIFT.md``, "Change classes").

Severity (``low``, ``medium``, ``high``) says how large a change is. The class says what it does to
the people who read the data: a removed column *breaks* them, an added one is *additive*, a mean
that moved is *cosmetic* (the schema and the constraints are unchanged, values moved).
``classify`` is the one place that decides; ``shape.diff``, the schema drift gate and the gates of
``shape.yml`` all call it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

CLASSES = ("breaking", "additive", "cosmetic")
#: how strict a class is: ``fail_on`` a class fails on it and on every stricter one
RANK = {"cosmetic": 0, "additive": 1, "breaking": 2}
BUMPS = ("major", "minor", "patch", "none")

#: the default class of every drift kind (``null_rate_change`` and ``uniqueness_change`` are
#: conditional: the value here is the class when their condition does not hold)
DEFAULT_CLASSES: dict[str, str] = {
    "table_removed": "breaking",
    "column_removed": "breaking",
    "dtype_change": "breaking",
    "pattern_change": "breaking",
    "dependency_broken": "breaking",
    "reference_match_change": "breaking",
    "null_rate_change": "cosmetic",
    "uniqueness_change": "cosmetic",
    "table_added": "additive",
    "column_added": "additive",
    "new_categorical_values": "additive",
    "row_count_change": "cosmetic",
    "cardinality_change": "cosmetic",
    "mean_shift": "cosmetic",
    "spread_change": "cosmetic",
    "distribution_shift": "cosmetic",
    "category_shift": "cosmetic",
    "true_rate_change": "cosmetic",
    "distribution_change": "cosmetic",
    "range_change": "cosmetic",
    "length_change": "cosmetic",
    "outlier_rate_change": "cosmetic",
    "hour_of_day_change": "cosmetic",
    "day_of_week_change": "cosmetic",
    "placeholder_surge": "cosmetic",
    "implausible_rate_change": "cosmetic",
    "association_shift": "cosmetic",
    "zero_inflation_change": "cosmetic",
    "heaping_change": "cosmetic",
    "benford_change": "cosmetic",
    "tail_change": "cosmetic",
    "multivariate_outlier_rate_change": "cosmetic",
    "structure_change": "cosmetic",
    "cohort_shift": "cosmetic",
    "mixture_change": "cosmetic",
    "seasonality_change": "cosmetic",
}

#: names a policy may give a class to that are not drift kinds
PSEUDO_KINDS = ("dtype_widening",)

_REASONS = {
    "table_removed": "a table that readers query was removed",
    "column_removed": "a column that readers select was removed",
    "dtype_change": "the column has another type",
    "pattern_change": "the values follow another pattern",
    "dependency_broken": "a dependency between columns no longer holds",
    "reference_match_change": "fewer rows are a real combination of the reference",
    "table_added": "a new table; no existing reader is affected",
    "column_added": "a new column; no existing reader is affected",
    "new_categorical_values": "a category the baseline did not have; no existing reader is "
    "affected",
}
_COSMETIC = "the schema and the constraints are unchanged, values moved"

_INT = re.compile(r"^(u?)int(8|16|32|64)$")
_DECIMAL = re.compile(r"^decimal(?:128|256)?\((\d+),\s*(-?\d+)\)$")
_ALIASES = {"str": "string", "utf8": "string", "large_utf8": "large_string", "double": "float64"}


@dataclass(frozen=True, slots=True)
class Classification:
    """The class of one change (``breaking``, ``additive`` or ``cosmetic``) and why."""

    class_: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"class": self.class_, "reason": self.reason}


# ---- policy: overrides -------------------------------------------------------------------------


def check_classes(classes: Any, where: str = "classes") -> dict[str, str]:
    """``{kind: class}`` as a plain dict. A kind is a drift kind or ``dtype_widening``; a class is
    one of :data:`CLASSES`. Anything else is a ``ValueError``."""
    if not isinstance(classes, Mapping):
        raise ValueError(f"drift policy {where!r} must be an object of kind to class")
    known = set(DEFAULT_CLASSES) | set(PSEUDO_KINDS)
    for kind, cls in classes.items():
        if kind not in known:
            raise ValueError(
                f"unknown change kind {kind!r} in {where} (drift kinds are listed in "
                "docs/DRIFT.md; dtype_widening is the other one)"
            )
        if cls not in CLASSES:
            raise ValueError(
                f"unknown class {cls!r} for {kind!r} in {where} "
                f"(use {', '.join(CLASSES)}; low, medium and high are severity levels, not classes)"
            )
    return dict(classes)


def check_column_classes(value: Any, where: str = "column_classes") -> dict[str, dict[str, str]]:
    """``{pattern: {kind: class}}``, checked as :func:`check_classes`."""
    if not isinstance(value, Mapping):
        raise ValueError(f"drift policy {where!r} must be an object of pattern to classes")
    out: dict[str, dict[str, str]] = {}
    for pattern, classes in value.items():
        out[str(pattern)] = check_classes(classes, f"{where}[{pattern!r}]")
    return out


# ---- widening ----------------------------------------------------------------------------------


def _norm(name: Any) -> str | None:
    if not isinstance(name, str):
        return None
    text = name.strip().lower()
    return _ALIASES.get(text, text) or None


def widening(baseline: Any, current: Any) -> bool:
    """True when the ``current`` Arrow type can hold every value of the ``baseline`` type: a
    signed integer to a wider signed one, an unsigned integer to a wider integer, ``float32`` to
    ``float64``, an integer of 32 bits or fewer to ``float64``, ``string`` to ``large_string``,
    ``binary`` to ``large_binary``, and a decimal to one with more precision and the same scale.
    Names are Arrow's (``int32``) or the numpy-style ones of the schema drift gate (``str``); a
    type family without a width (``integer``) is never a widening."""
    before, after = _norm(baseline), _norm(current)
    if before is None or after is None or before == after:
        return False
    b, a = _INT.fullmatch(before), _INT.fullmatch(after)
    if b and a:
        b_unsigned, b_bits = bool(b.group(1)), int(b.group(2))
        a_unsigned, a_bits = bool(a.group(1)), int(a.group(2))
        if not b_unsigned:
            return not a_unsigned and a_bits > b_bits
        return a_bits > b_bits
    if b and after == "float64":
        return int(b.group(2)) <= 32
    if before == "float32":
        return after == "float64"
    if before == "string":
        return after == "large_string"
    if before == "binary":
        return after == "large_binary"
    d, e = _DECIMAL.fullmatch(before), _DECIMAL.fullmatch(after)
    if d and e:
        return d.group(2) == e.group(2) and int(e.group(1)) > int(d.group(1))
    return False


def with_widening(record: dict[str, Any]) -> dict[str, Any]:
    """``record`` with ``detail.widening: true`` when it is a widening ``dtype_change``."""
    if record.get("kind") == "dtype_change" and widening(
        record.get("baseline"), record.get("current")
    ):
        detail = {**record.get("detail", {}), "widening": True}
        return {**record, "detail": detail}
    return record


# ---- the classifier ----------------------------------------------------------------------------


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def classify(change: Mapping[str, Any], classes: Mapping[str, str] | None = None) -> Classification:
    """The class of a change record (``kind``, ``baseline``, ``current`` and optionally
    ``detail``), by the defaults of ``docs/DRIFT.md`` and the policy's ``classes``
    (``{kind: class}``, ``dtype_widening`` included). An unknown kind, or an unknown kind or class
    in ``classes``, is a ``ValueError``."""
    kind = change.get("kind")
    if kind not in DEFAULT_CLASSES:
        raise ValueError(f"unknown change kind {kind!r}")
    over = check_classes(classes) if classes else {}
    detail = change.get("detail") or {}
    if kind == "dtype_change":
        wide = bool(detail.get("widening")) or widening(
            change.get("baseline"), change.get("current")
        )
        if wide and "dtype_widening" in over:
            return Classification(over["dtype_widening"], "a widening type change, set by policy")
        if kind in over:
            return Classification(over[kind], "set by policy")
        if wide:
            return Classification(
                "breaking",
                "the type was widened and every old value fits, but readers see another type "
                "(policy dtype_widening can class it additive)",
            )
        return Classification("breaking", _REASONS[kind])
    if kind in over:
        return Classification(over[kind], "set by policy")
    if kind == "null_rate_change":
        before, after = _number(change.get("baseline")), _number(change.get("current"))
        if before == 0 and after is not None and after > 0:
            return Classification(
                "breaking", "a column that never had nulls has them now (readers may not expect)"
            )
        return Classification("cosmetic", "the column already had nulls, or has fewer")
    if kind == "uniqueness_change":
        before, after = _number(change.get("baseline")), _number(change.get("current"))
        was_unique = bool(detail.get("baseline_primary_key")) or (
            before is not None and before >= 1.0
        )
        if was_unique and (after is None or after < 1.0):
            return Classification("breaking", "a primary key or unique column has duplicates now")
        return Classification("cosmetic", "the column was not unique, or still is")
    return Classification(DEFAULT_CLASSES[kind], _REASONS.get(kind, _COSMETIC))


# ---- summary, bump and gates -------------------------------------------------------------------


def bump_of(breaking: int, additive: int, cosmetic: int) -> str:
    """``major`` with any breaking change, else ``minor`` with any additive one, else ``patch``
    with any cosmetic one, else ``none``."""
    if breaking:
        return "major"
    if additive:
        return "minor"
    return "patch" if cosmetic else "none"


def _counts(changes: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    out = dict.fromkeys(CLASSES, 0)
    for c in changes:
        out[c["class"]] += 1
    return out


def summarise(
    changes: Sequence[Mapping[str, Any]],
    counted: Sequence[bool] | None = None,
    *,
    planned: bool = False,
) -> dict[str, Any]:
    """The ``semver`` block of a diff: the bump and the class counts of the unplanned changes
    (``counted`` marks them; ``None`` means all). With ``planned`` the others are counted under
    ``planned``."""
    flags = list(counted) if counted is not None else [True] * len(changes)
    own = _counts(c for c, f in zip(changes, flags, strict=True) if f)
    out: dict[str, Any] = {"bump": bump_of(own["breaking"], own["additive"], own["cosmetic"])}
    out.update(own)
    if planned:
        out["planned"] = _counts(c for c, f in zip(changes, flags, strict=True) if not f)
    return out


def check_fail_on(fail_on: Any) -> str:
    if fail_on not in CLASSES:
        raise ValueError(f"fail_on must be one of {', '.join(CLASSES)}, got {fail_on!r}")
    return str(fail_on)


def meets(cls: str, fail_on: str) -> bool:
    """True when ``cls`` is ``fail_on`` or stricter (breaking, then additive, then cosmetic)."""
    return RANK[cls] >= RANK[check_fail_on(fail_on)]


def fails(
    changes: Sequence[Mapping[str, Any]], counted: Sequence[bool] | None, fail_on: str
) -> bool:
    """True when an unplanned change of class ``fail_on`` or a stricter one is reported."""
    check_fail_on(fail_on)
    flags = list(counted) if counted is not None else [True] * len(changes)
    return any(f and meets(c["class"], fail_on) for c, f in zip(changes, flags, strict=True))


_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def next_version(version: str, bump: str) -> str:
    """``version`` (``X.Y.Z``) raised by ``bump``; ``none`` keeps it."""
    found = _VERSION.fullmatch(version) if isinstance(version, str) else None
    if not found:
        raise ValueError(f"a version is X.Y.Z (three numbers), got {version!r}")
    if bump not in BUMPS:
        raise ValueError(f"bump must be one of {', '.join(BUMPS)}, got {bump!r}")
    major, minor, patch = (int(g) for g in found.groups())
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    if bump == "patch":
        return f"{major}.{minor}.{patch + 1}"
    return version
