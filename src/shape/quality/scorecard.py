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
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from .gates import ValidationContext
from .rowlevel import CheckOutcome, row_outcomes, sample_failures
from .verify import VerifyResult
from .verifyconfig import VerifyConfig

FORMAT = "shape-scorecard"
VERSION = 1
SUPPRESSIONS_FORMAT = "shape-scorecard-suppressions"
SUPPRESSIONS_VERSION = 1
PROJECT_FILE = "shape.yml"
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
    Path(path).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


# -- owners ----------------------------------------------------------------------------------


def column_owners(project_dir: str | Path | None = None) -> dict[str, str] | None:
    """Column owners (``"table.column": owner``) from the ``owners`` mapping of the project file
    ``shape.yml`` in ``project_dir`` (default: the current directory). None when there is no
    project file; an empty mapping when the file names no owners."""
    p = Path(project_dir or ".") / PROJECT_FILE
    if not p.is_file():
        return None
    from shape.security.yamlsafe import safe_load_yaml

    try:
        doc = safe_load_yaml(p.read_text(encoding="utf-8"))
    except ImportError as exc:
        raise ScorecardError(f"reading {p} needs PyYAML: pip install pyyaml") from exc
    owners = (doc or {}).get("owners", {}) if isinstance(doc, dict) else {}
    if not isinstance(owners, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in owners.items()
    ):
        raise ScorecardError(f'{p}: "owners" must map "table.column" to an owner name')
    return dict(owners)


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

    def to_dict(self, include_samples: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "format": FORMAT,
            "version": VERSION,
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
        return "\n".join(lines) + "\n"


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
) -> Scorecard:
    """Score the gates of a verify ``result`` run on ``tables``, by dimension.

    ``schema`` and ``config`` are the ones the gates ran with. ``samples`` is how many failing
    rows to show per failing check (safe by default: ``classified`` columns, and any column that
    looks like personal data, show ``[redacted]`` unless ``show_classified``). ``history`` is
    the earlier scorecards of :func:`scorecard_trend`; give it to get a trend."""
    from shape import __version__

    if samples < 0:
        raise ScorecardError("samples must be zero or more")
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
        if version > VERSION:
            raise ScorecardError(
                f"{key} holds a scorecard of version {version}, newer than the version "
                f"{VERSION} this release reads; upgrade Shape"
            )
        dims = {
            d: v.get("score")
            for d, v in (doc.get("dimensions") or {}).items()
            if isinstance(v, dict)
        }
        points.append(
            {
                "at": datetime.fromtimestamp(entry.get("created_at", 0), UTC).isoformat(),
                "content_id": entry["content_id"],
                "dimensions": dims,
            }
        )
    return points
