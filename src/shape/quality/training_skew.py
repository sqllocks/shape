"""Training-serving skew: how a serving set differs from the training set it was meant to match.

Per feature (every column of the training data except the label): schema skew (missing in
serving, type changed), null-rate skew, the population stability index (the one of
``shape drift --psi``: :func:`shape.fidelity.tier3.psi_report`), the share of serving rows with a
category never seen in training, and the share of serving values outside the training minimum
and maximum. Features are ranked by PSI. With a slice column the same is computed per slice.

Either side may be a profile instead of data. A profile holds no values, so the PSI and the two
value measures are not computed for it (the report says so); the schema and null-rate skew are.

These are data checks, thresholds are screening defaults, and nothing here audits a model.
See ``docs/FAIRNESS_AND_SKEW.md``.
"""

from __future__ import annotations

import json
import math
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from .rowlevel import _missing_mask, is_classified
from .slicing import NULL_LABEL, POOL_LABEL, SliceError, _positive_mask, group_rows
from .sources import is_profile, load_data_or_profile

FORMAT = "shape-skew-report"
VERSION = 1

#: Screening defaults. ``psi`` is the documented 0.2 of ``shape drift --psi`` (flagged at 0.2 or
#: more); the others are this command's own and are settable (``--threshold KEY=VALUE``).
DEFAULT_THRESHOLDS: dict[str, float | int | None] = {
    "psi": 0.2,
    "null_rate": 0.05,
    "unseen_category_share": 0.01,
    "out_of_range_share": 0.01,
    "label_rate_diff": None,
    "min_slice_rows": 30,
}
#: A column with more distinct training values than this has no category measure (identifiers,
#: free text); the same limit as the PSI of text columns.
MAX_CATEGORIES = 50
_TOL = 10  # decimal places a difference is rounded to before it is compared with a threshold
_BASE_MEASURES = ("psi", "unseen_category_share", "out_of_range_share")


class SkewError(ValueError):
    """The training or serving input, or an option, cannot be used."""


def parse_thresholds(items: Sequence[str]) -> dict[str, float | int]:
    """``KEY=VALUE`` strings as a mapping, checked. Keys: ``psi``, ``null_rate``,
    ``unseen_category_share``, ``out_of_range_share``, ``label_rate_diff``, ``min_slice_rows``."""
    out: dict[str, float | int] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key or not value:
            raise SkewError(f"a threshold is KEY=VALUE, got {item!r}")
        out[key.strip()] = _threshold(key.strip(), value.strip())
    return out


def _threshold(key: str, raw: Any) -> float | int:
    if key not in DEFAULT_THRESHOLDS:
        raise SkewError(f"unknown threshold {key!r}; the keys are {', '.join(DEFAULT_THRESHOLDS)}")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise SkewError(f"threshold {key} must be a number, got {raw!r}") from None
    if math.isnan(value) or math.isinf(value):
        raise SkewError(f"threshold {key} must be a finite number, got {raw!r}")
    if key == "min_slice_rows":
        if value != int(value):
            raise SkewError("threshold min_slice_rows must be a whole number")
        if value < 1:
            raise SkewError("threshold min_slice_rows must be 1 or more")
        return int(value)
    if value < 0:
        raise SkewError(f"threshold {key} must be zero or more")
    return value


# -- columns -----------------------------------------------------------------------------------


def _kind(t: pa.DataType) -> str:
    if pa.types.is_dictionary(t):
        t = t.value_type
    if pa.types.is_boolean(t):
        return "boolean"
    if pa.types.is_integer(t):
        return "integer"
    if pa.types.is_floating(t) or pa.types.is_decimal(t):
        return "float"
    if pa.types.is_string(t) or pa.types.is_large_string(t):
        return "string"
    if pa.types.is_timestamp(t) or pa.types.is_date(t):
        return "datetime"
    return "other"


def _plain(col: Any) -> Any:
    if isinstance(col, pa.ChunkedArray):
        col = col.combine_chunks()
    if pa.types.is_dictionary(col.type):
        col = col.cast(col.type.value_type)
    return col


def _null_rate(col: Any) -> float:
    n = len(col)
    return float(_missing_mask(col).sum()) / n if n else 0.0


class _Side:
    """One table of either side: its data, or only what a profile says of its columns."""

    def __init__(self, table: pa.Table | None, view: Any = None) -> None:
        self.table = table
        self.kinds: dict[str, str] = {}
        self.null_rates: dict[str, float] = {}
        self.label_rates: dict[str, float | None] = {}
        if table is not None:
            self.rows = table.num_rows
            for name in table.column_names:
                col = table.column(name)
                self.kinds[name] = _kind(col.type)
                self.null_rates[name] = _null_rate(col)
        else:
            self.rows = int(view.rows)
            for name, v in view.columns.items():
                self.kinds[name] = str(v.dtype)
                self.null_rates[name] = float(v.null_rate or 0.0)
                self.label_rates[name] = v.true_rate

    @property
    def columns(self) -> list[str]:
        return list(self.kinds)


def _sides(obj: Any, fmt: str) -> dict[str, _Side]:
    if isinstance(obj, (str, Path)):
        obj = load_data_or_profile(obj, fmt)
    if isinstance(obj, pa.Table):
        obj = {"table": obj}
    if is_profile(obj):
        from shape.drift.engine import tables_of

        views, _ = tables_of(obj)
        return {n: _Side(None, v) for n, v in views.items()}
    if isinstance(obj, Mapping) and all(isinstance(v, pa.Table) for v in obj.values()):
        return {str(n): _Side(t) for n, t in obj.items()}
    raise SkewError(f"expected data or a profile, not {type(obj).__name__}")


# -- the measures ------------------------------------------------------------------------------


def _r4(x: float | None) -> float | None:
    return None if x is None else round(float(x), 4)


def _unseen(train: Any, serving: Any) -> tuple[float | None, str | None]:
    t, s = _plain(train), _plain(serving)
    seen = pc.unique(pc.drop_null(t))
    if len(seen) > MAX_CATEGORIES:
        return None, f"more than {MAX_CATEGORIES} distinct values in training"
    valid = pc.invert(pc.is_null(s))
    n = pc.sum(valid.cast(pa.int64())).as_py() or 0
    if not n:
        return None, "no serving values"
    known = pc.fill_null(pc.is_in(s, value_set=seen), False)
    unseen = pc.sum(pc.and_(valid, pc.invert(known)).cast(pa.int64())).as_py() or 0
    return unseen / n, None


def _outside(train: Any, serving: Any, kind: str) -> tuple[float | None, str | None]:
    t, s = _plain(train), _plain(serving)
    if kind == "float" or pa.types.is_decimal(t.type):
        t, s = t.cast(pa.float64()), s.cast(pa.float64())
    if pa.types.is_floating(t.type):
        t = pc.filter(t, pc.invert(pc.fill_null(pc.is_nan(t), False)))
    if pa.types.is_floating(s.type):
        s = pc.filter(s, pc.invert(pc.fill_null(pc.is_nan(s), False)))
    lo_hi = pc.min_max(t)
    lo, hi = lo_hi["min"], lo_hi["max"]
    if not lo.is_valid or not hi.is_valid:
        return None, "no training values"
    s = pc.drop_null(s)
    if not len(s):
        return None, "no serving values"
    if s.type != t.type:
        if pa.types.is_integer(t.type) and pa.types.is_floating(s.type):
            lo, hi = lo.cast(pa.float64()), hi.cast(pa.float64())
        elif pa.types.is_floating(t.type) and pa.types.is_integer(s.type):
            s = s.cast(pa.float64())
        else:
            return None, "the column type differs"
    out = pc.or_(pc.less(s, lo), pc.greater(s, hi))
    return (pc.sum(out.cast(pa.int64())).as_py() or 0) / len(s), None


def _feature_entries(
    train: _Side,
    serving: _Side,
    features: Sequence[str],
    th: Mapping[str, Any],
    mode: str,
) -> list[dict[str, Any]]:
    """The measures of every feature of one pair of tables (or slices), ranked by PSI."""
    psi: dict[str, float | None] = {}
    skipped: dict[str, str] = {}
    if mode == "data":
        from shape.fidelity.tier3 import psi_report

        both = [f for f in features if f in serving.kinds]
        assert train.table is not None and serving.table is not None
        report = psi_report(train.table.select(both), serving.table.select(both), th["psi"])
        for name in both:
            res = report.columns.get(name)
            if res is not None and res.method == "psi":
                psi[name] = res.psi
            elif res is not None:
                skipped[name] = "the column type differs between training and serving"
            elif name in report.skipped:
                skipped[name] = report.skipped[name]
            else:
                skipped[name] = "fewer than 10 values on one side"
    entries: list[dict[str, Any]] = []
    for name in features:
        notes: dict[str, str] = {}
        flags: list[str] = []
        kt = train.kinds[name]
        ks = serving.kinds.get(name)
        status = "missing_in_serving" if ks is None else "type_changed" if ks != kt else "ok"
        if status != "ok":
            flags.append("schema")
        e: dict[str, Any] = {
            "feature": name,
            "schema": {"status": status, "train_type": kt, "serving_type": ks},
            "train_null_rate": _r4(train.null_rates[name]),
            "serving_null_rate": None,
            "null_rate_difference": None,
            "psi": None,
            "unseen_category_share": None,
            "out_of_range_share": None,
        }
        if ks is None:
            for m in _BASE_MEASURES:
                notes[m] = "the column is missing in serving"
        else:
            diff = serving.null_rates[name] - train.null_rates[name]
            e["serving_null_rate"] = _r4(serving.null_rates[name])
            e["null_rate_difference"] = _r4(diff)
            if round(abs(diff), _TOL) > th["null_rate"]:
                flags.append("null_rate")
            if mode == "profile":
                for m in _BASE_MEASURES:
                    notes[m] = "needs data on both sides; a profile holds no values"
            else:
                _value_measures(e, notes, flags, train, serving, name, kt, ks, th, psi, skipped)
        e["notes"] = notes
        e["flags"] = flags
        e["flagged"] = bool(flags)
        entries.append(e)
    entries.sort(key=lambda e: (e["psi"] is None, -(e["psi"] or 0.0), e["feature"]))
    return entries


def _value_measures(
    e: dict[str, Any],
    notes: dict[str, str],
    flags: list[str],
    train: _Side,
    serving: _Side,
    name: str,
    kt: str,
    ks: str,
    th: Mapping[str, Any],
    psi: Mapping[str, float | None],
    skipped: Mapping[str, str],
) -> None:
    assert train.table is not None and serving.table is not None
    value = psi.get(name)
    if value is None:
        notes["psi"] = skipped.get(name, "not computed")
    else:
        e["psi"] = value
        if value >= th["psi"]:
            flags.append("psi")
    if kt == "string" and ks == "string":
        share, why = _unseen(train.table.column(name), serving.table.column(name))
        e["unseen_category_share"] = _r4(share)
        if why:
            notes["unseen_category_share"] = why
        elif share is not None and round(share, _TOL) > th["unseen_category_share"]:
            flags.append("unseen_category_share")
    else:
        notes["unseen_category_share"] = "not a text column"
    if kt in ("integer", "float", "datetime") and ks in ("integer", "float", "datetime"):
        share, why = _outside(train.table.column(name), serving.table.column(name), kt)
        e["out_of_range_share"] = _r4(share)
        if why:
            notes["out_of_range_share"] = why
        elif share is not None and round(share, _TOL) > th["out_of_range_share"]:
            flags.append("out_of_range_share")
    else:
        notes["out_of_range_share"] = "not a numeric or date column"


# -- the label ---------------------------------------------------------------------------------


def _rate(table: pa.Table, label: str) -> float | None:
    try:
        _, pos, labelled = _positive_mask(table, label)
    except SliceError as exc:
        raise SkewError(str(exc)) from None
    n = int(labelled.sum())
    return float(pos.sum()) / n if n else None


def _label_entry(
    train: _Side, serving: _Side, label: str, th: Mapping[str, Any], mode: str
) -> dict[str, Any]:
    if label not in train.kinds:
        raise SkewError(f"label {label!r} is not a column of the training data")
    if mode == "data":
        assert train.table is not None and serving.table is not None
        tr = _rate(train.table, label)
        sr = _rate(serving.table, label) if label in serving.kinds else None
    else:
        tr = train.label_rates.get(label)
        sr = serving.label_rates.get(label) if label in serving.kinds else None
    diff = None if tr is None or sr is None else sr - tr
    limit = th["label_rate_diff"]
    flagged = bool(diff is not None and limit is not None and round(abs(diff), _TOL) > limit)
    out: dict[str, Any] = {
        "column": label,
        "train_rate": _r4(tr),
        "serving_rate": _r4(sr),
        "difference": _r4(diff),
        "flagged": flagged,
    }
    if sr is None:
        out["note"] = (
            "the label is not in the serving data" if label not in serving.kinds else "no rate"
        )
    return out


# -- slices ------------------------------------------------------------------------------------


def _slices(
    name: str,
    train: _Side,
    serving: _Side,
    column: str,
    features: Sequence[str],
    th: Mapping[str, Any],
    classified: Mapping[str, Collection[str]] | None,
) -> dict[str, Any]:
    assert train.table is not None and serving.table is not None
    floor = int(th["min_slice_rows"])
    gt = group_rows(train.table.select([column]), [column])
    gs = group_rows(serving.table.select([column]), [column])
    big = [
        k for k in set(gt) | set(gs) if len(gt.get(k, ())) >= floor and len(gs.get(k, ())) >= floor
    ]
    small = [k for k in set(gt) | set(gs) if k not in big]
    safe = is_classified(name, column, train.table, classified)

    def text(k: tuple[str | None, ...]) -> str:
        return NULL_LABEL if k[0] is None else str(k[0])

    big.sort(key=lambda k: (-(len(gt.get(k, ())) + len(gs.get(k, ()))), text(k)))
    feats = [f for f in features if f != column]

    def one(label: str, t_rows: Any, s_rows: Any) -> dict[str, Any]:
        assert train.table is not None and serving.table is not None
        ts = _Side(train.table.take(pa.array(np.sort(t_rows))))
        ss = _Side(serving.table.take(pa.array(np.sort(s_rows))))
        entries = _feature_entries(ts, ss, feats, th, "data")
        return {
            "slice": label,
            "train_rows": ts.rows,
            "serving_rows": ss.rows,
            "features": entries,
            "flagged": any(e["flagged"] for e in entries),
        }

    empty = np.zeros(0, dtype=np.int64)
    shown = [
        one(f"slice {i}" if safe else text(k), gt.get(k, empty), gs.get(k, empty))
        for i, k in enumerate(big, 1)
    ]
    pooled_t = np.concatenate([gt.get(k, empty) for k in small]) if small else empty
    pooled_s = np.concatenate([gs.get(k, empty) for k in small]) if small else empty
    reported = len(small) >= 2
    if reported:
        shown.append(one(POOL_LABEL, pooled_t, pooled_s))
    return {
        "by": column,
        "slices": shown,
        "small_slices": {
            "slices": len(small),
            "train_rows": len(pooled_t),
            "serving_rows": len(pooled_s),
            "reported": reported,
        },
    }


# -- the report --------------------------------------------------------------------------------


class SkewReport:
    """The skew of a serving set against a training set. ``flagged`` is True when a feature (or a
    slice's feature, or a table) is flagged; the command exits 1 then."""

    def __init__(self, doc: dict[str, Any]) -> None:
        self._doc = doc

    @property
    def flagged(self) -> bool:
        return bool(self._doc["flagged"])

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = json.loads(json.dumps(self._doc))
        return out

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self._doc, indent=indent, allow_nan=False) + "\n"

    def to_markdown(self) -> str:
        d = self._doc
        lines = [
            "# Shape training-serving skew",
            "",
            f"**Result:** {'FLAGGED' if d['flagged'] else 'no skew flagged'}  ",
            f"**Shape version:** {d['shape_version']}  ",
            "**Thresholds:** "
            + ", ".join(f"{k}={v}" for k, v in d["thresholds"].items() if v is not None),
        ]
        if d["missing_tables"]:
            lines += ["", f"Tables missing in serving: {', '.join(d['missing_tables'])}"]
        for name, t in d["tables"].items():
            lines += ["", f"## {name}", ""]
            lines += [
                f"{t['train_rows']:,} training rows, {t['serving_rows']:,} serving rows"
                + (" (profiles: schema and null-rate skew only)" if t["mode"] == "profile" else ""),
                "",
            ]
            lines += _feature_table(t["features"])
            if t["extra_in_serving"]:
                lines += ["", f"Only in serving: {', '.join(t['extra_in_serving'])}"]
            lab = t.get("label")
            if lab:
                lines += ["", _label_line(lab)]
            sl = t.get("slices")
            if sl:
                for s in sl["slices"]:
                    mark = " (flagged)" if s["flagged"] else ""
                    lines += [
                        "",
                        f"### {name}, {sl['by']} = {s['slice']}{mark}",
                        "",
                        f"{s['train_rows']:,} training rows, {s['serving_rows']:,} serving rows",
                        "",
                    ]
                    lines += _feature_table(s["features"])
                if sl["small_slices"]["slices"] and not sl["small_slices"]["reported"]:
                    lines += ["", "One slice below the minimum size is not shown."]
        return "\n".join(lines) + "\n"


def _num(x: Any) -> str:
    return "" if x is None else f"{x:g}"


def _feature_table(features: Sequence[Mapping[str, Any]]) -> list[str]:
    lines = [
        "| Feature | PSI | Null rate (train, serving) | Unseen category share | "
        "Out of range share | Schema | Flags |",
        "|---------|-----|----------------------------|-----------------------|"
        "--------------------|--------|-------|",
    ]
    for f in features:
        lines.append(
            f"| {f['feature']} | {_num(f['psi'])} | "
            f"{_num(f['train_null_rate'])}, {_num(f['serving_null_rate'])} | "
            f"{_num(f['unseen_category_share'])} | {_num(f['out_of_range_share'])} | "
            f"{f['schema']['status']} | {', '.join(f['flags'])} |"
        )
    return lines


def _label_line(lab: Mapping[str, Any]) -> str:
    text = f"Label `{lab['column']}`: training rate {_num(lab['train_rate'])}"
    if lab["serving_rate"] is None:
        return f"{text}; {lab.get('note', 'no serving rate')}"
    flag = " **flagged**" if lab["flagged"] else ""
    return (
        text
        + f", serving rate {_num(lab['serving_rate'])}, difference {_num(lab['difference'])}{flag}"
    )


def skew(
    train: Any,
    serving: Any,
    *,
    features: Sequence[str] | None = None,
    label: str | None = None,
    slice_by: str | None = None,
    thresholds: Mapping[str, float | int] | None = None,
    classified: Mapping[str, Collection[str]] | None = None,
    fmt: str = "auto",
) -> SkewReport:
    """How ``serving`` differs from ``train``, feature by feature.

    ``train`` and ``serving`` are data (a path, an Arrow table, or a mapping of table name to
    Arrow table) or a profile (a ``.shape`` artifact, the object of ``shape.load`` or a
    profile-engine document). ``features`` limits the columns (default: every training column
    except ``label``); ``label`` is a boolean or two-valued column that is left out of the
    features and whose positive rate is compared; ``slice_by`` names one column to repeat the
    measures per slice (slices under ``thresholds["min_slice_rows"]`` rows on either side are
    pooled, never shown alone). ``thresholds`` overrides :data:`DEFAULT_THRESHOLDS`. Returns a
    :class:`SkewReport`; its ``to_dict()`` is the ``shape-skew-report`` document."""
    from shape import __version__

    th: dict[str, Any] = dict(DEFAULT_THRESHOLDS)
    for key, value in (thresholds or {}).items():
        th[key] = _threshold(key, value)
    left, right = _sides(train, fmt), _sides(serving, fmt)
    if not left:
        raise SkewError("the training input has no tables")
    if not right:
        raise SkewError("the serving input has no tables")
    if len(left) == 1 and len(right) == 1:
        pairs = {next(iter(left)): (next(iter(left.values())), next(iter(right.values())))}
        missing: list[str] = []
    else:
        pairs = {n: (t, right[n]) for n, t in left.items() if n in right}
        missing = [n for n in left if n not in right]
    if not pairs:
        raise SkewError("no table is in both the training and the serving input")
    if features is not None:
        for f in features:
            if not any(f in t.kinds for t, _ in pairs.values()):
                raise SkewError(f"feature {f!r} is not a column of the training data")
    tables: dict[str, Any] = {}
    sliced_any = False
    for name, (t, s) in pairs.items():
        mode = "data" if t.table is not None and s.table is not None else "profile"
        if slice_by is not None and mode == "profile":
            raise SkewError("slicing needs data on both sides, not a profile")
        names = [c for c in t.columns if c != label]
        feats = [c for c in names if c in features] if features is not None else names
        entry: dict[str, Any] = {
            "mode": mode,
            "train_rows": t.rows,
            "serving_rows": s.rows,
            "features": _feature_entries(t, s, feats, th, mode),
            "extra_in_serving": [c for c in s.columns if c not in t.kinds and c != label],
            "label": None,
            "slices": None,
        }
        if label is not None:
            entry["label"] = _label_entry(t, s, label, th, mode)
        if slice_by is not None and slice_by in t.kinds and slice_by in s.kinds:
            entry["slices"] = _slices(name, t, s, slice_by, feats, th, classified)
            sliced_any = True
        entry["flagged"] = bool(
            any(f["flagged"] for f in entry["features"])
            or (entry["label"] and entry["label"]["flagged"])
            or (entry["slices"] and any(x["flagged"] for x in entry["slices"]["slices"]))
        )
        tables[name] = entry
    if slice_by is not None and not sliced_any:
        raise SkewError(
            f"slice column {slice_by!r} is not a column of both the training and the serving data"
        )
    doc = {
        "format": FORMAT,
        "version": VERSION,
        "shape_version": __version__,
        "thresholds": th,
        "flagged": bool(missing) or any(t["flagged"] for t in tables.values()),
        "missing_tables": missing,
        "tables": tables,
    }
    return SkewReport(doc)
