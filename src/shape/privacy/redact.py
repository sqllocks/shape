"""Safe-by-default capture: what a profile keeps when it is written or printed (W1-11).

``shape.profile`` returns the full profile in memory: exact extremes, every enum value, the top
values with their shares. What Shape *persists or prints* (``shape profile -o``, ``--json``,
``--html``, the profile registry, :func:`shape.save`) is the **safe capture** unless the caller
asks for ``capture="full"``:

* a **sensitive** column keeps statistics and formats only: counts, the distinct estimate, type,
  the length distribution, the detected pattern and its rates, the quantile fingerprint and
  numeric ``bounds`` taken from it. It keeps no enum values, top values, placeholders or raw
  extremes. A column is sensitive when its declared classification ranks at or above
  ``CONFIDENTIAL`` in the one taxonomy of ``docs/PRIVACY_MODEL.md``, or when the safe profile's own
  rules make it pattern-only (:func:`shape.privacy.safe_profile.pii_gate_reason`);
* any other column keeps category values only when it is an enum by the profiler's rule and every
  released category stands for at least ``k`` rows; smaller categories fold into ``__OTHER__``
  exactly as :mod:`shape.privacy.cells` does for the safe profile, and histogram bins and top-value
  entries follow the same cell rule;
* the artifact records how it was captured (``capture`` and ``redaction_manifest``), and every
  column that lost a surface says which in its ``redacted`` map (surface to reason), so a reader
  can tell "suppressed" from "absent" and report a change or a rule it cannot evaluate. A map
  rather than a list: the leak scanner of ``shape profile validate --safe`` reads a list of more
  than two strings as a value dump.

The output has the shape of a full profile, so every reader of profiles keeps working. Redaction
is idempotent and one way: a profile captured safe cannot be made full again.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .cells import OTHER_BUCKET, non_null_base, suppress_bins, suppress_weights
from .classification import DEFAULT_TAXONOMY
from .safe_profile import (
    K_DEFAULT,
    SafeConfig,
    _column_rates,
    _winsorized_bounds,
    pii_gate_reason,
)

MODES = ("safe", "full")
DEFAULT_MODE = "safe"
SENSITIVE_FLOOR = "CONFIDENTIAL"

# What the tools print when something asks for real values to be kept.
FULL_CAPTURE_WARNING = (
    "shape: warning: --capture full keeps real values in {output}; do not commit or share it"
)

_GATE = SafeConfig()  # the safe profile's own pattern-only rules, at their defaults
_PROPORTION_PLACES = 4  # the conditionals of the joint analysis are rounded to 4 places
SENSITIVE = "sensitive"  # the surface was removed because the column is sensitive
BELOW_K = "below_k"  # ...because it stood for fewer than k rows
_NO_TEXT = frozenset({"integer", "float", "decimal", "boolean", "date", "datetime", "timestamp"})


@dataclass(frozen=True)
class CaptureConfig:
    """How a capture is made. The defaults are the safe ones.

    ``classifications`` maps a column (``name`` or ``table.name``) to its declared classification;
    ``column_k`` maps a column to its own minimum cohort.
    """

    mode: str = DEFAULT_MODE
    k: int = K_DEFAULT
    column_k: Mapping[str, int] = field(default_factory=dict)
    classifications: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"capture must be one of {', '.join(MODES)}, not {self.mode!r}")
        _check_k(self.k, "k")
        for name, n in self.column_k.items():
            _check_k(n, f"k of column {name!r}")
        for name, label in self.classifications.items():
            DEFAULT_TAXONOMY.canonical(label)  # an unknown label is an error, never ignored
            if not isinstance(name, str) or not name:
                raise ValueError("a classification names a column")

    def is_sensitive_label(self, label: str) -> bool:
        return DEFAULT_TAXONOMY.at_least(label, SENSITIVE_FLOOR)


def _check_k(n: Any, what: str) -> None:
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError(f"{what} must be an integer of at least 1, not {n!r}")


# --- what a reader may ask of a column -----------------------------------------------------------


def suppressed_surfaces(column: Mapping[str, Any]) -> frozenset[str]:
    """The surfaces of a column dict that a safe capture removed (empty for a full capture)."""
    redacted = column.get("redacted")
    return frozenset(str(s) for s in redacted) if isinstance(redacted, Mapping) else frozenset()


def has_folded_categories(column: Mapping[str, Any]) -> bool:
    """True when the column's categories were folded into ``__OTHER__`` or its top values cut."""
    enum = column.get("enum_values")
    return (isinstance(enum, Mapping) and OTHER_BUCKET in enum) or bool(
        {"enum_values", "value_counts_ext"} & suppressed_surfaces(column)
    )


def is_captured_safe(column: Mapping[str, Any]) -> bool:
    """True when anything was suppressed from the column."""
    return bool(suppressed_surfaces(column)) or has_folded_categories(column)


# --- the column ----------------------------------------------------------------------------------


def _lower_bound(proportion: float, base: int, places: int) -> int:
    """A row count the proportion certainly stands for, whatever it was rounded to."""
    slack = 0.5 * 10**-places * base
    return int(max(0, math.ceil(proportion * base - slack - 1e-9)))


def _tag_in(tag: Any, keys: Iterable[str]) -> bool:
    """True when a typed minimum or maximum (``["int", 5]``) is one of the category keys."""
    if not (isinstance(tag, list) and len(tag) == 2):
        return False
    value = tag[1]
    texts = set(keys)
    if isinstance(value, bool):
        return str(value) in texts or str(value).lower() in texts
    if isinstance(value, int | float):
        for key in texts:
            try:
                if float(key) == float(value):
                    return True
            except ValueError:
                continue
        return False
    return str(value) in texts


@dataclass
class _ColumnResult:
    column: dict[str, Any]
    entry: dict[str, Any]


def _redact_column(
    col: Mapping[str, Any],
    *,
    k: int,
    classified: bool,
    row_count: int,
    sampled_rows: int,
    previous: Mapping[str, Any],
) -> _ColumnResult:
    c = copy.deepcopy(dict(col))
    dtype = str(c.get("dtype"))
    cardinality = int(c.get("cardinality") or 0)
    base = non_null_base(sampled_rows, c.get("null_count"), c.get("null_rate") or 0.0)
    pii = pii_gate_reason(c.get("pattern"), cardinality, row_count, _GATE, _column_rates(c))
    reason = "classification" if classified else pii
    sensitive = reason is not None
    suppressed: dict[str, str] = {}
    for earlier in (previous.get("suppressed"), c.get("redacted")):
        if isinstance(earlier, Mapping):
            suppressed.update({str(key): str(why) for key, why in earlier.items()})
    dropped = 0
    cells = 0
    why_removed = SENSITIVE if sensitive else BELOW_K

    def remove(key: str) -> None:
        if c.get(key) is not None:
            suppressed.setdefault(key, why_removed)
        c[key] = None

    was_enum = bool(c.get("is_enum"))
    enum = c.get("enum_values")
    counts = c.get("value_counts_ext")

    if sensitive:
        for key in ("enum_values", "value_counts_ext", "min_value", "max_value"):
            remove(key)
        c["is_enum"] = False
        if c.pop("placeholders", None):
            suppressed.setdefault("placeholders", SENSITIVE)
        quantiles = c.get("quantiles")
        if isinstance(quantiles, Mapping):
            bounds = _winsorized_bounds(quantiles, _GATE)
            if bounds is not None:
                c["bounds"] = bounds
            if c.get("distribution") not in (None, "normal"):
                # uniform, exponential and lognormal fits name the minimum (and the range)
                for key in ("distribution", "distribution_params", "fit_score"):
                    c[key] = None
                suppressed.setdefault("distribution_params", SENSITIVE)
    else:
        released_real: dict[str, float] = {}
        if isinstance(enum, Mapping) and enum:
            released, folded = suppress_weights(enum, k, base)
            dropped += folded
            released_real = {key: v for key, v in released.items() if key != OTHER_BUCKET}
            if released_real:
                c["enum_values"] = released
            else:  # nothing, or one bucket that says nothing
                remove("enum_values")
                c["is_enum"] = False
        elif enum is not None:
            remove("enum_values")
            c["is_enum"] = False
        if isinstance(counts, Mapping) and counts:
            if released_real:
                kept = {key: v for key, v in counts.items() if key in released_real}
            elif was_enum:
                kept = {}
            else:
                kept = {
                    key: v
                    for key, v in counts.items()
                    if _lower_bound(float(v), base, 6) >= k or key == OTHER_BUCKET
                }
            if len(kept) != len(counts):
                suppressed.setdefault("value_counts_ext", BELOW_K)
            c["value_counts_ext"] = kept or None
        elif counts is not None:
            c["value_counts_ext"] = None
        # a minimum or maximum that is a category value is a value like the others
        keys = set(released_real)
        released_all = (
            isinstance(c.get("enum_values"), Mapping) and OTHER_BUCKET not in c["enum_values"]
        )
        for key in ("min_value", "max_value"):
            tag = c.get(key)
            if tag is None:
                continue
            is_value_like = dtype not in _NO_TEXT or was_enum
            if is_value_like and not (released_all and was_enum) and not _tag_in(tag, keys):
                remove(key)
        kept_holders = []
        for holder in c.get("placeholders") or ():
            n = holder.get("count") if isinstance(holder, Mapping) else None
            if isinstance(n, int) and n >= k:
                kept_holders.append(holder)
        if c.get("placeholders") and len(kept_holders) != len(c["placeholders"]):
            suppressed.setdefault("placeholders", BELOW_K)
        if kept_holders:
            c["placeholders"] = kept_holders
        else:
            c.pop("placeholders", None)

    c["value_counts_ext_order"] = None

    # every other cell surface obeys the same minimum cohort
    for key in ("hour_histogram", "dow_histogram"):
        bins = c.get(key)
        if isinstance(bins, list):
            released_bins, n = suppress_bins(bins, k, base)
            cells += n
            c[key] = released_bins
            if released_bins is None:
                suppressed.setdefault(key, BELOW_K)
    temporal = c.get("temporal_histogram")
    if isinstance(temporal, dict):
        for part in ("year_weights", "month_weights"):
            weights = temporal.get(part)
            if isinstance(weights, list):
                released_bins, n = suppress_bins(weights, k, base)
                cells += n
                if released_bins is None:
                    del temporal[part]
                    suppressed.setdefault("temporal_histogram", BELOW_K)
                else:
                    temporal[part] = released_bins

    if suppressed:
        c["redacted"] = dict(sorted(suppressed.items()))
    else:
        c.pop("redacted", None)
    prior_k = int(previous.get("k") or 0)
    entry = {
        "categories_dropped": int(previous.get("categories_dropped") or 0) + dropped,
        "cells_suppressed": int(previous.get("cells_suppressed") or 0) + cells,
        "bounds_winsorized": "bounds" in c,
        "pattern_only": pii is not None,
        "k": max(k, prior_k),
        "sensitive": sensitive,
        "reason": reason,
        "suppressed": dict(sorted(suppressed.items())),
    }
    return _ColumnResult(c, entry)


# --- joint analysis, findings, reference pairs ---------------------------------------------------


_MULTIVARIATE_JOINT = ("multivariate_outliers", "pca", "cohorts", "copula")


def _redact_joint(joint: Mapping[str, Any], k: int, sensitive: set[str]) -> dict[str, Any]:
    j = copy.deepcopy(dict(joint))
    j.pop("columns", None)  # a list of names, not a statistic
    j.pop("categorical_columns", None)  # the same, for two-column determinants (W3-08)
    # The multivariate entries (W3-08) name their columns in lists and carry real values: the
    # copula's categories and a cohort's most common value. A safe capture keeps none of them.
    for key in _MULTIVARIATE_JOINT:
        j.pop(key, None)
    conditionals = []
    for cond in j.get("conditionals") or ():
        if cond.get("given") in sensitive or cond.get("target") in sensitive:
            continue
        rows: dict[str, Any] = {}
        for label, entry in (cond.get("table") or {}).items():
            n = int(entry.get("n") or 0)
            if n < k:
                continue
            p = {
                target: share
                for target, share in (entry.get("p") or {}).items()
                if _lower_bound(float(share), n, _PROPORTION_PLACES) >= k
            }
            rows[label] = {"n": n, "p": p}
        if rows:
            conditionals.append({**cond, "table": rows})
    if "conditionals" in j:
        j["conditionals"] = conditionals
    for dep in j.get("dependencies") or ():
        involved = set(dep.get("determinant") or ()) | {dep.get("dependent")}
        keep = []
        for v in dep.get("violations") or ():
            counts = list((v.get("dependent_values") or {}).values())
            if involved & sensitive or int(v.get("rows") or 0) < k or any(n < k for n in counts):
                continue
            keep.append(v)
        if "violations" in dep:
            dep["violations"] = keep
    for pair in j.get("reference_pairs") or ():
        if "examples" not in pair:
            continue
        locked = set(pair.get("columns") or ()) & sensitive
        pair["examples"] = (
            [] if locked else [e for e in pair["examples"] if int(e.get("rows") or 0) >= k]
        )
    return j


def _redact_findings(
    findings: list[Mapping[str, Any]], k: int, sensitive: Mapping[str, set[str]]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for finding in findings:
        f = {key: v for key, v in finding.items() if key not in ("examples", "cells")}
        if f.get("kind") == "sentinel_values":
            if f.get("column") in sensitive.get(str(f.get("table")), set()):
                continue
            if int(f.get("count") or 0) < k:
                continue
        out.append(f)
    return out


# --- the profile ---------------------------------------------------------------------------------


def _redact_merge(merge: Mapping[str, Any], *, dataset: bool) -> dict[str, Any]:
    """A merged profile's lineage block (W2-01) without the top values of its merged sketches:
    they are real values with their counts, and a safe capture keeps none."""
    m = copy.deepcopy(dict(merge))
    per_table = m["sketch_columns"] if dataset else {"": m["sketch_columns"]}
    for columns in per_table.values():
        for entry in (columns or {}).values():
            if isinstance(entry, dict):
                entry.pop("top", None)
                if isinstance(entry.get("error_models"), dict):
                    entry["error_models"].pop("top", None)
    return m


def _tables_of(data: Mapping[str, Any]) -> dict[str, Any]:
    return dict(data["tables"]) if "tables" in data else {str(data["name"]): data}


def _resolve(key: str, tables: Mapping[str, Any]) -> list[tuple[str, str]]:
    """The ``(table, column)`` pairs a column name stands for; an unknown name is an error."""
    exact = [(t, key.removeprefix(f"{t}.")) for t in tables if key.startswith(f"{t}.")]
    exact = [(t, c) for t, c in exact if c in tables[t]["columns"]]
    if exact:
        return exact
    bare = [(t, key) for t in tables if key in tables[t]["columns"]]
    if not bare:
        raise ValueError(f"there is no column {key!r} to apply a capture setting to")
    return bare


def redact_profile(profile: Any, config: CaptureConfig | None = None) -> Any:
    """The capture of ``profile`` under ``config``, as a new ``Profile``.

    ``profile`` is a ``shape.profile`` result or an earlier capture; it is never modified. With
    ``mode="full"`` the content is the profile's own and is stamped full (a profile that was
    captured safe cannot be made full: re-profile the data). With ``mode="safe"`` the sensitive
    columns keep statistics and formats only and the categories need ``k`` rows each, and the
    sketch state of ``shape.profile(..., sketches=True)`` is not kept (it holds real values; the
    redaction manifest says so).
    """
    cfg = config or CaptureConfig()
    data = profile.to_dict()
    earlier = profile.capture if profile.capture_declared else None
    if cfg.mode == "full":
        if earlier is not None and earlier["mode"] == "safe":
            raise ValueError(
                "this profile was captured safe: its real values are gone, so it cannot be "
                "saved as a full capture; profile the data again with capture='full'"
            )
        return profile.with_capture({"mode": "full", "k": None}, {})

    tables = _tables_of(data)
    classified: dict[tuple[str, str], str] = {}
    for key, label in cfg.classifications.items():
        for target in _resolve(key, tables):
            classified[target] = label
    column_k: dict[tuple[str, str], int] = {}
    for key, n in cfg.column_k.items():
        for target in _resolve(key, tables):
            column_k[target] = n
    before = profile.redaction_manifest.get("tables", {}) if earlier else {}
    prior_k = int((earlier or {}).get("k") or 0) if earlier and earlier["mode"] == "safe" else 0
    default_k = max(cfg.k, prior_k)

    sensitive_by_table: dict[str, set[str]] = {}
    multivariate_removed = False
    manifest_tables: dict[str, Any] = {}
    for tname, table in tables.items():
        row_count = int(table.get("row_count") or 0)
        # Sample support controls category disclosure, not the existing population-based
        # sensitivity classification. Keeping those separate preserves source round trips.
        sampled_rows = int(table.get("sampled_rows", row_count) or 0)
        sensitive: set[str] = set()
        entries: dict[str, Any] = {}
        columns: dict[str, Any] = {}
        for cname, col in table["columns"].items():
            declared = classified.get((tname, cname))
            result = _redact_column(
                col,
                k=column_k.get((tname, cname), cfg.k),
                classified=declared is not None and cfg.is_sensitive_label(declared),
                row_count=row_count,
                sampled_rows=sampled_rows,
                previous=before.get(tname, {}).get(cname, {}),
            )
            columns[cname] = result.column
            entries[cname] = result.entry
            if result.entry["sensitive"]:
                sensitive.add(cname)
        table["columns"] = columns
        sensitive_by_table[tname] = sensitive
        manifest_tables[tname] = entries
        if isinstance(table.get("joint"), Mapping):
            if any(key in table["joint"] for key in _MULTIVARIATE_JOINT):
                multivariate_removed = True
            table["joint"] = _redact_joint(table["joint"], cfg.k, sensitive)
        if isinstance(table.get("findings"), list):
            table["findings"] = _redact_findings(table["findings"], cfg.k, sensitive_by_table)
    if "tables" in data and isinstance(data.get("findings"), list):
        data["findings"] = _redact_findings(data["findings"], cfg.k, sensitive_by_table)
    manifest: dict[str, Any] = {"unsafe": False, "k_default": default_k, "tables": manifest_tables}
    merge = data.get("merge")
    if isinstance(merge, Mapping) and isinstance(merge.get("sketch_columns"), Mapping):
        data["merge"] = _redact_merge(merge, dataset="tables" in data)
        manifest["merge"] = "removed: the top values of the merged sketches"
    if earlier:  # a safe capture saved again keeps what its first redaction recorded (idempotent)
        for key in ("joint", "sketches", "merge"):
            if key in profile.redaction_manifest:
                manifest[key] = profile.redaction_manifest[key]
    if multivariate_removed:
        manifest["joint"] = (
            "removed: the multivariate entries (multivariate_outliers, pca, cohorts, copula) "
            "name columns in lists and hold real values"
        )
    if profile.sketches is not None:
        # the sketch state (W2-01) holds sampled and top values of every column: never safe
        manifest["sketches"] = (
            "removed: the sketch state holds real values (capture='full' keeps it)"
        )
    redacted = type(profile)(data, name=profile.name, provenance=profile.provenance)
    return redacted.with_capture({"mode": "safe", "k": default_k}, manifest)
