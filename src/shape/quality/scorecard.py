"""Data quality scorecards: one score per dimension, from the existing validation gates.

The six dimensions are the standard names: accuracy, completeness, conformity, consistency,
timeliness and uniqueness. :data:`GATE_DIMENSION` maps each gate of :mod:`shape.quality.gates` to
exactly one of them (documented in ``docs/SCORECARD.md`` and tested against it). A check is one
gate on one table and column set. Where a gate has a row-level form
(:mod:`shape.quality.rowlevel`) the check scores ``100 * (1 - failing rows / rows)``; any other
gate is one check scoring 100 when it passes and 0 when it fails. The gates of the ``reconcile``
and ``timeseries`` rules (:data:`CONFIG_GATE_DIMENSION`) are scored the same way; the memorization
and utility gates compare generated data with its source and are not data quality checks. A
dimension scores the mean of its checks, and is not scored (``None``) when no check of it ran.

Known issues are recorded in a versioned file (``format: shape-scorecard-suppressions``): a
*snooze* hides a failing check from the score until a date, a *suppress* hides it with a reason
and no end. A hidden check is still listed, with its reason, under ``known_issues``.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from .gates import ValidationContext
from .rowlevel import CheckOutcome, row_outcomes, sample_failures
from .slicing import (
    DEFAULT_MIN_SLICE_ROWS,
    SliceError,
    build_slices,
    gap_trend,
    slice_gaps,
)
from .verify import VerifyResult
from .verifyconfig import VerifyConfig

FORMAT = "shape-scorecard"
#: A scorecard without slices is written as version 1, one with ``slices`` as version 2; this
#: release reads both.
VERSION = 1
SLICED_VERSION = 2
READ_VERSION = SLICED_VERSION
SUPPRESSIONS_FORMAT = "shape-scorecard-suppressions"
SUPPRESSIONS_VERSION = 1
HISTORY_PREFIX = "scorecard-"

DIMENSIONS: tuple[str, ...] = (
    "accuracy",
    "completeness",
    "conformity",
    "consistency",
    "timeliness",
    "uniqueness",
)

#: Which dimension each validation gate scores. One gate, one dimension.
GATE_DIMENSION: dict[str, str] = {
    "distribution": "accuracy",
    "range_constraint": "accuracy",
    "null_constraint": "completeness",
    "file_format": "conformity",
    "schema_conformance": "conformity",
    "schema_drift": "conformity",
    "referential_integrity": "consistency",
    "temporal_consistency": "timeliness",
    "unique_constraint": "uniqueness",
}

#: Gates that run only when the verify configuration has their rules (not registered built-ins).
CONFIG_GATE_DIMENSION: dict[str, str] = {
    "reconciliation": "consistency",
    "timeseries_quality": "timeliness",
}
_SCORED_GATES = {**GATE_DIMENSION, **CONFIG_GATE_DIMENSION}


class ScorecardError(ValueError):
    """A scorecard, its history or its project file cannot be used."""


class SuppressionError(ScorecardError):
    """A snooze and suppress file cannot be used."""


# -- snooze and suppress ---------------------------------------------------------------------

_ENTRY_KEYS = {"action", "check", "table", "column", "until", "reason"}


@dataclass(frozen=True, slots=True)
class Suppression:
    """A known issue. ``table`` and ``column`` left out match any."""

    action: str
    check: str
    reason: str
    table: str | None = None
    column: str | None = None
    until: date | None = None

    def active(self, today: date) -> bool:
        return self.action == "suppress" or (self.until is not None and today <= self.until)

    def matches(self, gate: str, table: str | None, columns: tuple[str, ...]) -> bool:
        if gate != self.check:
            return False
        if self.table is not None and self.table != table:
            return False
        return self.column is None or self.column in columns

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"action": self.action, "check": self.check}
        if self.table is not None:
            out["table"] = self.table
        if self.column is not None:
            out["column"] = self.column
        if self.until is not None:
            out["until"] = self.until.isoformat()
        out["reason"] = self.reason
        return out


def _entry(raw: Any, i: int) -> Suppression:
    where = f"entries[{i}]"
    if not isinstance(raw, dict):
        raise SuppressionError(f"{where} must be an object")
    extra = sorted(set(raw) - _ENTRY_KEYS)
    if extra:
        raise SuppressionError(f"{where}: unknown key {extra[0]!r}")
    action = raw.get("action")
    if action not in ("snooze", "suppress"):
        raise SuppressionError(f'{where}: action must be "snooze" or "suppress", not {action!r}')
    check = raw.get("check")
    if check not in _SCORED_GATES:
        raise SuppressionError(f"{where}: check {check!r} is not one of {sorted(_SCORED_GATES)}")
    reason = raw.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise SuppressionError(f"{where}: a reason is required")
    until: date | None = None
    if action == "snooze":
        if "until" not in raw:
            raise SuppressionError(f"{where}: a snooze needs an until date (YYYY-MM-DD)")
        try:
            until = date.fromisoformat(str(raw["until"]))
        except ValueError:
            raise SuppressionError(
                f"{where}: until {raw['until']!r} is not a date (YYYY-MM-DD)"
            ) from None
    elif "until" in raw:
        raise SuppressionError(f"{where}: a suppress has no until date; use snooze for that")
    for key in ("table", "column"):
        if key in raw and (not isinstance(raw[key], str) or not raw[key]):
            raise SuppressionError(f"{where}: {key} must be a non-empty string")
    return Suppression(action, check, reason, raw.get("table"), raw.get("column"), until)


def load_suppressions(path: str | Path) -> list[Suppression]:
    """The entries of a snooze and suppress file. A newer ``version`` than this release reads is
    refused rather than guessed at."""
    p = Path(path)
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SuppressionError(f"{p} is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("format") != SUPPRESSIONS_FORMAT:
        raise SuppressionError(f'{p}: format must be "{SUPPRESSIONS_FORMAT}"')
    version = doc.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise SuppressionError(f"{p}: version must be an integer of 1 or more")
    if version > SUPPRESSIONS_VERSION:
        raise SuppressionError(
            f"{p} is version {version}, newer than the version {SUPPRESSIONS_VERSION} this "
            "release reads; upgrade Shape"
        )
    entries = doc.get("entries")
    if not isinstance(entries, list):
        raise SuppressionError(f"{p}: entries must be a list")
    return [_entry(e, i) for i, e in enumerate(entries)]


def save_suppressions(path: str | Path, entries: Iterable[Suppression]) -> None:
    doc = {
        "format": SUPPRESSIONS_FORMAT,
        "version": SUPPRESSIONS_VERSION,
        "entries": [e.to_dict() for e in entries],
    }
    Path(path).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")


# -- owners ----------------------------------------------------------------------------------


class ProjectOwners(Mapping[str, str]):
    """The column owners of a project file (``sources.NAME.columns.COLUMN.owner``, W1-04).

    A key ``"table.column"`` is looked up the way ``shape diff`` and ``shape check`` do it
    (``table.column``, then the column name, then a matching glob), in each source in name
    order; the first owner found wins. Iterating gives the column keys that name an owner."""

    def __init__(self, sources: Sequence[Any]) -> None:
        self._sources = list(sources)
        self._keys = sorted(
            {k for s in self._sources for k, c in s.columns.items() if c.owner is not None}
        )

    def __getitem__(self, key: str) -> str:
        table, _, column = key.rpartition(".")
        for source in self._sources:
            owner = source.owner_of(column, table or None)
            if owner:
                return str(owner)
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(self._keys)

    def __len__(self) -> int:
        return len(self._keys)


def column_owners(project_dir: str | Path | None = None) -> ProjectOwners | None:
    """Column owners from the project file ``shape.yml`` (or ``shape.yaml``) in ``project_dir``
    (default: the current directory): the ``owner`` of each source's column settings, read and
    validated as ``shape project validate`` does. None when there is no project file; empty when
    the file names no owners. An invalid project file raises ``ProjectError``."""
    from shape.project import load_project
    from shape.project.file import FILE_NAMES

    folder = Path(project_dir or ".")
    found = next((folder / n for n in FILE_NAMES if (folder / n).is_file()), None)
    if found is None:
        return None
    project = load_project(found)
    return ProjectOwners([project.sources[n] for n in sorted(project.sources)])


# -- the scorecard ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CheckScore:
    gate: str
    table: str | None
    column: str | None
    label: str
    score: float
    rows: int | None
    failing: int | None
    owner: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "table": self.table,
            "column": self.column,
            "check": self.label,
            "score": self.score,
            "rows": self.rows,
            "failing": self.failing,
            "owner": self.owner,
        }


@dataclass(frozen=True, slots=True)
class DimensionScore:
    score: float | None
    checks: tuple[CheckScore, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"score": self.score, "checks": [c.to_dict() for c in self.checks]}


@dataclass
class Scorecard:
    dimensions: dict[str, DimensionScore]
    overall: float | None
    run_at: str
    data_path: str
    shape_version: str
    known_issues: list[dict[str, Any]] = field(default_factory=list)
    samples: list[dict[str, Any]] = field(default_factory=list)
    trend: dict[str, dict[str, Any]] | None = None
    #: The row-level checks that count toward the score (not those hidden as known issues).
    outcomes: list[CheckOutcome] = field(default_factory=list, repr=False)
    #: Scores by slice, representation and outcome rates (W3-11); None without ``slice_by``.
    slices: dict[str, Any] | None = None

    @property
    def version(self) -> int:
        return VERSION if self.slices is None else SLICED_VERSION

    def to_dict(self, include_samples: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "format": FORMAT,
            "version": self.version,
            "shape_version": self.shape_version,
            "run_at": self.run_at,
            "data_path": self.data_path,
            "overall": self.overall,
            "dimensions": {d: s.to_dict() for d, s in self.dimensions.items()},
            "known_issues": self.known_issues,
        }
        if include_samples:
            out["samples"] = self.samples
        if self.trend is not None:
            out["trend"] = self.trend
        if self.slices is not None:
            out["slices"] = self.slices
        return out

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    def to_markdown(self) -> str:
        lines = [
            "# Shape data quality scorecard",
            "",
            f"**Generated:** {self.run_at}  ",
            f"**Data path:** {self.data_path}  ",
            f"**Shape version:** {self.shape_version}  ",
            f"**Overall:** {_fmt(self.overall)}",
            "",
            "| Dimension | Score | Trend | Checks | Failing |",
            "|-----------|-------|-------|--------|---------|",
        ]
        for dim, d in self.dimensions.items():
            t = (self.trend or {}).get(dim)
            failing = sum(1 for c in d.checks if c.score < 100)
            lines.append(
                f"| {dim} | {_fmt(d.score)} | {_trend_text(t)} | {len(d.checks)} | {failing} |"
            )
        failing_checks = [
            (dim, c) for dim, d in self.dimensions.items() for c in d.checks if c.score < 100
        ]
        if failing_checks:
            lines += [
                "",
                "## Failing checks",
                "",
                "| Dimension | Check | Table | Columns | Score | Failing rows | Owner |",
                "|-----------|-------|-------|---------|-------|--------------|-------|",
            ]
            for dim, c in failing_checks:
                lines.append(
                    f"| {dim} | {c.gate} | {c.table or ''} | {c.column or ''} | {c.score:g} | "
                    f"{'' if c.failing is None else f'{c.failing:,}'} | {c.owner or ''} |"
                )
        if self.known_issues:
            lines += ["", "## Known issues", ""]
            for k in self.known_issues:
                scope = ".".join(x for x in (k["table"], k["column"]) if x) or "all"
                until = f" until {k['until']}" if k.get("until") else ""
                lines.append(f"- **{k['check']}** ({scope}): {k['action']}{until} - {k['reason']}")
        if self.samples:
            lines += [
                "",
                "## Failing-row samples",
                "",
                "Rows are 0-based. A classified column shows `[redacted]`.",
                "",
                "| Check | Table | Row | Values |",
                "|-------|-------|-----|--------|",
            ]
            for s in self.samples:
                lines.append(
                    f"| {s['gate']} | {s['table']} | {s['row']} | "
                    f"{json.dumps(s['values'], default=str)} |"
                )
        if self.slices is not None:
            lines += _slices_markdown(self.slices)
        return "\n".join(lines) + "\n"


def _slices_markdown(sl: Mapping[str, Any]) -> list[str]:
    lines = [
        "",
        "## Slices",
        "",
        f"Sliced by {', '.join(sl['by'])}; slices under "
        f"{sl['min_slice_rows']} rows are pooled as `(small slices)`.",
        "",
    ]
    trend = sl.get("trend") or {}
    for table, td in sl["tables"].items():
        lines += [f"### {table}", ""]
        lines += [
            "| Dimension | Gap | Worst slice | Trend |",
            "|-----------|-----|-------------|-------|",
        ]
        for dim, v in td["dimensions"].items():
            t = trend.get(f"{table}.{dim}")
            gap = "n/a" if v["gap"] is None else f"{v['gap']:g}"
            lines.append(f"| {dim} | {gap} | {v['worst_slice'] or ''} | {_slice_trend_text(t)} |")
        header = ["Slice", "Rows", "Share"]
        has_ref = any("reference_share" in e for e in td["slices"])
        has_label = "label" in td
        if has_ref:
            header += ["Reference share", "Ratio"]
        if has_label:
            header += ["Positive rate"]
        lines += ["", "| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
        for e in td["slices"]:
            row = [e["slice"], f"{e['rows']:,}", f"{e['share']:g}"]
            if has_ref:
                row += [f"{e['reference_share']:g}", _fmt(e["ratio"])]
            if has_label:
                row += [_fmt(e.get("positive_rate"))]
            lines.append("| " + " | ".join(row) + " |")
        if td["small_slices"]["slices"] and not td["small_slices"]["reported"]:
            lines += ["", "One slice below the minimum size is not shown."]
        for m in td.get("missing_from_data", []):
            lines.append(
                f"- In the reference but not in the data: {m['slice']} "
                f"(reference share {m['reference_share']:g})"
            )
        lab = td.get("label")
        if lab:
            ratio = _fmt(lab["disparity_ratio"])
            flag = (
                f" **below {lab['threshold']:g}: flagged** (four-fifths screening heuristic, "
                "not a legal test)"
                if lab["flagged"]
                else ""
            )
            lines += [
                "",
                f"Disparity ratio of `{lab['column']}` (lowest rate over highest): {ratio}{flag}",
            ]
        if td["null_rate_flags"]:
            lines += ["", "Null rates more than 0.1 above the table's:", ""]
            for f in td["null_rate_flags"]:
                lines.append(
                    f"- {f['slice']}: {f['column']} {f['null_rate']:g} "
                    f"(table {f['table_null_rate']:g})"
                )
        lines.append("")
    if "max_slice_gap" in sl:
        if sl["exceeded"]:
            lines.append(f"Gaps above {sl['max_slice_gap']:g}:")
            lines += [f"- {x['table']}.{x['dimension']}: {x['gap']:g}" for x in sl["exceeded"]]
        else:
            lines.append(f"No gap above {sl['max_slice_gap']:g}.")
    return lines


def _slice_trend_text(t: Mapping[str, Any] | None) -> str:
    if not t or t.get("direction") == "no data":
        return "-"
    return f"{t['direction']} ({t['change']:+g})"


def _fmt(score: float | None) -> str:
    return "n/a" if score is None else f"{score:g}"


def _trend_text(t: Mapping[str, Any] | None) -> str:
    if not t or t.get("direction") == "no data":
        return "-"
    return f"{t['direction']} ({t['change']:+g})"


def _owner_of(
    owners: Mapping[str, str] | None, table: str | None, columns: Sequence[str]
) -> str | None:
    if not owners or table is None:
        return None
    for c in columns:
        o = owners.get(f"{table}.{c}")
        if o:
            return o
    return None


#: The highest score a check, dimension or overall mean can show while something fails.
_NOT_PERFECT = 99.99


def _score(value: float, failing: bool) -> float:
    """``value`` rounded to two decimals, but never 100 while something fails."""
    out = round(value, 2)
    return min(out, _NOT_PERFECT) if failing and out >= 100.0 else out


def _mean(values: Sequence[float]) -> float | None:
    return _score(sum(values) / len(values), min(values) < 100.0) if values else None


def _trend(
    dimensions: Mapping[str, DimensionScore], history: Sequence[Mapping[str, Any]]
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    last = history[-1].get("dimensions", {}) if history else {}
    for dim, d in dimensions.items():
        prev = last.get(dim)
        if d.score is None or prev is None:
            out[dim] = {"previous": prev, "change": None, "direction": "no data"}
            continue
        change = round(d.score - prev, 2)
        direction = "improving" if change > 0 else "declining" if change < 0 else "steady"
        out[dim] = {"previous": prev, "change": change, "direction": direction}
    return out


def build_scorecard(
    result: VerifyResult,
    tables: Mapping[str, pa.Table],
    *,
    schema: Any = None,
    config: VerifyConfig | None = None,
    suppressions: Collection[Suppression] = (),
    owners: Mapping[str, str] | None = None,
    today: date | None = None,
    samples: int = 5,
    classified: Mapping[str, Collection[str]] | None = None,
    show_classified: bool = False,
    history: Sequence[Mapping[str, Any]] | None = None,
    slice_by: Sequence[str] | None = None,
    min_slice_rows: int = DEFAULT_MIN_SLICE_ROWS,
    label: str | None = None,
    reference: Any = None,
    max_slice_gap: float | None = None,
) -> Scorecard:
    """Score the gates of a verify ``result`` run on ``tables``, by dimension.

    ``schema`` and ``config`` are the ones the gates ran with. ``samples`` is how many failing
    rows to show per failing check (safe by default: ``classified`` columns, and any column that
    looks like personal data, show ``[redacted]`` unless ``show_classified``). ``history`` is
    the earlier scorecards of :func:`scorecard_trend`; give it to get a trend.

    ``slice_by`` (column names) also scores every dimension per slice, with each slice's share of
    rows (and, given ``reference``, data or a profile of the population, the reference share and
    the ratio), the positive rate and disparity ratio of a boolean or two-valued ``label`` column,
    and null rates; the card then has ``slices`` and is written as version 2. Slices under
    ``min_slice_rows`` rows are pooled and never shown alone. ``max_slice_gap`` records which gaps
    exceed it (``slices["exceeded"]``)."""
    from shape import __version__

    if samples < 0:
        raise ScorecardError("samples must be zero or more")
    if slice_by is None and (label is not None or reference is not None):
        raise ScorecardError("label and reference need slice_by")
    if slice_by is None and max_slice_gap is not None:
        raise ScorecardError("max_slice_gap needs slice_by")
    day = today or datetime.now(UTC).date()
    live = [s for s in suppressions if s.active(day)]
    ctx = ValidationContext(
        tables=dict(tables), schema=schema, config=dict(config.rules) if config else {}
    )
    by_dim: dict[str, list[CheckScore]] = {d: [] for d in DIMENSIONS}
    known: list[dict[str, Any]] = []
    counted: list[CheckOutcome] = []

    def hidden(gate: str, table: str | None, columns: tuple[str, ...]) -> Suppression | None:
        return next((s for s in live if s.matches(gate, table, columns)), None)

    def note(
        s: Suppression, gate: str, table: str | None, columns: tuple[str, ...], n: int | None
    ) -> None:
        known.append(
            {
                **s.to_dict(),
                "check": gate,
                "table": table,
                "column": ",".join(columns) or None,
                "failing": n,
                "owner": _owner_of(owners, table, columns),
            }
        )

    for g in result.gate_results:
        dim = _SCORED_GATES.get(g.gate_name)
        if dim is None:
            continue
        outcomes = row_outcomes(g.gate_name, ctx)
        if outcomes is None or (not outcomes and g.errors):
            columns: tuple[str, ...] = ()
            s = None if g.passed else hidden(g.gate_name, None, columns)
            if s is not None:
                note(s, g.gate_name, None, columns, None)
                continue
            by_dim[dim].append(
                CheckScore(g.gate_name, None, None, "gate", 100.0 if g.passed else 0.0, None, None)
            )
            continue
        if not g.passed and not any(o.failing for o in outcomes):
            # the gate failed for a reason no row-level check shows (a missing column or table):
            # the failure is its own check, beside the row-level ones, not a score of 100
            s = hidden(g.gate_name, None, ())
            if s is not None:
                note(s, g.gate_name, None, (), None)
            else:
                by_dim[dim].append(CheckScore(g.gate_name, None, None, "gate", 0.0, None, None))
        for o in outcomes:
            s = hidden(o.gate, o.table, o.columns) if o.failing else None
            if s is not None:
                note(s, o.gate, o.table, o.columns, o.failing)
                continue
            counted.append(o)
            score = (
                100.0 if o.rows == 0 else _score(100.0 * (1 - o.failing / o.rows), o.failing > 0)
            )
            by_dim[dim].append(
                CheckScore(
                    o.gate,
                    o.table,
                    ",".join(o.columns),
                    o.label,
                    score,
                    o.rows,
                    o.failing,
                    _owner_of(owners, o.table, o.columns),
                )
            )
    dimensions = {
        d: DimensionScore(_mean([c.score for c in checks]), tuple(checks))
        for d, checks in by_dim.items()
    }
    scored = [d.score for d in dimensions.values() if d.score is not None]
    slices: dict[str, Any] | None = None
    if slice_by is not None:
        row_gates = [g.gate_name for g in result.gate_results if g.gate_name in GATE_DIMENSION]

        def score_slice(name: str, sub: pa.Table) -> dict[str, float | None]:
            sub_ctx = ValidationContext(
                tables={**ctx.tables, name: sub}, schema=schema, config=ctx.config
            )
            found: dict[str, list[float]] = {d: [] for d in DIMENSIONS}
            for gate in row_gates:
                for o in row_outcomes(gate, sub_ctx) or ():
                    if o.table != name or (o.failing and hidden(o.gate, o.table, o.columns)):
                        continue
                    found[GATE_DIMENSION[gate]].append(
                        100.0 if o.rows == 0 else round(100.0 * (1 - o.failing / o.rows), 2)
                    )
            return {d: _mean(v) for d, v in found.items()}

        try:
            slices = build_slices(
                tables,
                slice_by,
                score=score_slice,
                min_slice_rows=min_slice_rows,
                label=label,
                reference=reference,
                classified=classified,
                show_classified=show_classified,
                max_slice_gap=max_slice_gap,
            )
        except SliceError as exc:
            raise ScorecardError(str(exc)) from None
        if history is not None:
            last = history[-1].get("slice_gaps") if history else None
            slices["trend"] = gap_trend(slice_gaps(slices), last)
    return Scorecard(
        dimensions=dimensions,
        overall=_mean(scored),
        run_at=datetime.now(UTC).isoformat(),
        data_path=result.data_path,
        shape_version=__version__,
        known_issues=known,
        samples=sample_failures(
            counted, tables, limit=samples, classified=classified, show_classified=show_classified
        ),
        trend=_trend(dimensions, history) if history is not None else None,
        outcomes=counted,
        slices=slices,
    )


# -- trends over the registry history --------------------------------------------------------


def record_scorecard(registry: Any, name: str, card: Scorecard) -> str:
    """Commit the scores of ``card`` to ``registry`` under ``scorecard-<name>`` and return the
    content id. Samples are not stored: the history keeps scores only."""
    return str(
        registry.commit(
            f"{HISTORY_PREFIX}{name}",
            json.dumps(card.to_dict(include_samples=False), indent=2, default=str),
            {"kind": FORMAT, "overall": card.overall},
        )
    )


def scorecard_trend(registry: Any, name: str) -> list[dict[str, Any]]:
    """The scores of every scorecard committed under ``name``, oldest first. Entries that are not
    scorecards are skipped; a scorecard of a newer version than this release reads is refused."""
    key = f"{HISTORY_PREFIX}{name}"
    points: list[dict[str, Any]] = []
    for entry in registry.log(key):
        try:
            doc = json.loads(registry.checkout(key, entry["content_id"]))
        except (ValueError, KeyError, OSError):
            continue
        if not isinstance(doc, dict) or doc.get("format") != FORMAT:
            continue
        version = doc.get("version")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            continue
        if version > READ_VERSION:
            raise ScorecardError(
                f"{key} holds a scorecard of version {version}, newer than the version "
                f"{READ_VERSION} this release reads; upgrade Shape"
            )
        dims = {
            d: v.get("score")
            for d, v in (doc.get("dimensions") or {}).items()
            if isinstance(v, dict)
        }
        point: dict[str, Any] = {
            "at": datetime.fromtimestamp(entry.get("created_at", 0), UTC).isoformat(),
            "content_id": entry["content_id"],
            "dimensions": dims,
        }
        if isinstance(doc.get("slices"), dict):
            point["slice_gaps"] = slice_gaps(doc["slices"])
        points.append(point)
    return points
