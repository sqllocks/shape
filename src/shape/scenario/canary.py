"""Canaries: a small, marked synthetic batch with known planted failures, to prove a pipeline's
checks still fire.

``make`` writes the batch (every row carries a marker column so downstream jobs can filter it
out) and ``canary.json`` (format ``shape-canary``): which scenario made it and which detections
are expected. You send the batch through a real pipeline input on a schedule; ``check`` reads the
``shape-result`` documents of your own ``shape diff``, ``shape check`` and ``shape verify`` and
names every expected detection that is missing: a blind spot of your monitoring.

A canary is one batch with defects, so it comes from a scenario that plants defects (not from a
change over time), and from a failure mode that some check of Shape detects. It is written to a
local folder only. See ``docs/CANARIES.md``. Nothing heavy loads at import time (T-18).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shape.scenario import results
from shape.scenario.library import catalog, detect, formats
from shape.scenario.library.formats import LibraryError, check_header, read_json

CANARY_FORMAT = "shape-canary"
FORMATS = ("csv", "parquet", "jsonl")
DEFAULT_ROWS = 1000
DEFAULT_MARKER = ("shape_canary", "1")
_KEYS = {"format", "version", "id", "scenario", "seed", "marker", "expected"}


class CanaryError(LibraryError):
    """A canary cannot be made, or a canary file or result is malformed."""


# ---- the document -------------------------------------------------------------------------------


def parse_canary(doc: Any, what: str = "the canary") -> dict[str, Any]:
    """The canary document ``doc``. Raises :class:`CanaryError` for another format, a missing or
    newer version, missing or unknown keys, a marker that is not ``{column, value}`` text, an
    ``expected`` list that is empty or holds a check Shape does not have."""
    try:
        out = check_header(doc, CANARY_FORMAT, what, _KEYS)
    except LibraryError as exc:
        raise CanaryError(str(exc)) from None
    missing = sorted(_KEYS - set(out))
    if missing:
        raise CanaryError(f"{what} lacks {', '.join(missing)}")
    for key in ("id", "scenario"):
        if not isinstance(out[key], str) or not out[key].strip():
            raise CanaryError(f"{what}: {key!r} must be text")
    if isinstance(out["seed"], bool) or not isinstance(out["seed"], int):
        raise CanaryError(f"{what}: 'seed' must be an integer")
    marker = out["marker"]
    if (
        not isinstance(marker, dict)
        or set(marker) != {"column", "value"}
        or not all(isinstance(v, str) and v for v in marker.values())
    ):
        raise CanaryError(f'{what}: \'marker\' is {{"column": TEXT, "value": TEXT}}')
    expected = out["expected"]
    if not isinstance(expected, list) or not expected:
        raise CanaryError(f"{what}: 'expected' must be a non-empty list of checks")
    for item in expected:
        try:
            for alternative in str(item).split("|"):
                detect.check_exists(alternative, f"{what}: an expected check")
        except LibraryError as exc:
            raise CanaryError(str(exc)) from None
    return out


def load_canary(path: str | Path) -> dict[str, Any]:
    try:
        doc = read_json(Path(path), "the canary")
    except LibraryError as exc:
        raise CanaryError(str(exc)) from None
    return parse_canary(doc, f"canary {Path(path).name}")


# ---- make ---------------------------------------------------------------------------------------


@dataclass
class Plan:
    """What ``make`` writes (or, with ``dry_run``, would write)."""

    id: str
    scenario: str
    seed: int
    directory: str
    marker: dict[str, str]
    expected: list[str]
    files: list[str] = field(default_factory=list)
    rows: int = 0
    dry_run: bool = False

    def document(self) -> dict[str, Any]:
        return {
            "format": CANARY_FORMAT,
            "version": formats.VERSION,
            "id": self.id,
            "scenario": self.scenario,
            "seed": self.seed,
            "marker": self.marker,
            "expected": self.expected,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.document(),
            "directory": self.directory,
            "files": self.files,
            "rows": self.rows,
            "dry_run": self.dry_run,
        }


def parse_marker(text: str) -> tuple[str, str]:
    """``("column", "value")`` of ``COLUMN=VALUE``."""
    column, sep, value = text.partition("=")
    if not sep or not column.strip() or not value.strip():
        raise CanaryError(f"--marker must be COLUMN=VALUE, got {text!r}")
    return column.strip(), value.strip()


def _target(target: str, root: Path | None) -> tuple[str, dict[str, Any] | None]:
    """``(scenario, catalog mode)`` for ``library:NAME`` or a failure mode id."""
    if target.startswith("library:"):
        name = target.partition(":")[2]
        mode = next((m for m in catalog.load_catalog(root) if catalog.scenario_of(m) == name), None)
        return name, mode
    mode = catalog.get_mode(target, root)
    return catalog.scenario_of(mode), mode


def _expected(scenario: str, mode: dict[str, Any] | None, root: Path | None) -> list[str]:
    from shape.scenario.library.run import load_expect

    if mode is not None:
        if not mode["detected_by"]:
            raise CanaryError(
                f"no check of Shape detects {mode['id']!r} ({mode['gap']}), so a canary of it "
                f"would prove nothing"
            )
        return list(mode["detected_by"])
    key = load_expect(scenario, root)
    expected = [f"gate:{g}" for g in key["gates_fail"]]
    for window in key["drift"]:
        expected += ["|".join(f"drift:{k}" for k in c["kinds"]) for c in window["changes"]]
    if not expected:
        raise CanaryError(
            f"the answer key of {scenario!r} expects no detection, so a canary of it would "
            f"prove nothing; use a failure mode of the catalog (`shape failure-modes list`)"
        )
    return expected


def _write_table(table: Any, path: Path, fmt: str) -> None:
    if fmt == "parquet":
        import pyarrow.parquet as pq  # type: ignore[import-untyped]

        pq.write_table(table, path)
    elif fmt == "csv":
        import pyarrow.csv as pacsv  # type: ignore[import-untyped]

        pacsv.write_csv(table, path)
    else:
        with path.open("w", encoding="utf-8") as fh:
            for row in table.to_pylist():
                fh.write(json.dumps(row, default=str) + "\n")


def make(
    target: str,
    directory: str | Path,
    *,
    rows: int = DEFAULT_ROWS,
    seed: int | None = None,
    marker: tuple[str, str] = DEFAULT_MARKER,
    fmt: str = "csv",
    dry_run: bool = False,
    root: Path | None = None,
) -> Plan:
    """Make the canary of ``target`` (``library:NAME`` or a failure mode id) in ``directory``.

    The batch has ``rows`` rows in every table the scenario touches, each carrying the marker
    column. The detections to expect are those of the catalog entry (or the scenario's answer
    key); each is checked to fire for this batch before anything is written. With ``dry_run``
    nothing is written. Raises :class:`CanaryError` for a target that cannot make a canary, a
    folder that is not a new or empty local folder, a marker that collides with a column and a
    check that does not fire at this size."""
    import pyarrow as pa  # type: ignore[import-untyped]

    if fmt not in FORMATS:
        raise CanaryError(f"--format must be one of {', '.join(FORMATS)}")
    if "://" in str(directory):
        raise CanaryError("a canary is written to a local folder, not to a remote target")
    out = Path(directory)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise CanaryError(f"{out} exists and is not an empty folder: write the canary to a new one")
    column, value = marker
    scenario, mode = _target(target, root)
    expected = _expected(scenario, mode, root)
    batch = detect.data_batch(scenario, seed=seed, rows=rows, root=root)
    fired = {d.check for d in detect.observe(batch.clean, batch.current, batch.touched)}
    fired |= {d.check for d in detect.gate_detections(batch.gates, batch.touched)}
    silent = [e for e in expected if not results.satisfied(e, fired)]
    if silent:
        raise CanaryError(
            f"at {rows} rows {', '.join(silent)} does not fire for {scenario!r}: make the "
            f"canary larger (--rows), or leave that check out of your expectations"
        )
    plan = Plan(
        id=f"canary-{scenario}-{batch.seed}",
        scenario=f"library:{scenario}",
        seed=batch.seed,
        directory=str(out),
        marker={"column": column, "value": value},
        expected=expected,
        rows=rows,
        dry_run=dry_run,
    )
    tables = {}
    for name in batch.touched:
        table = batch.current[name]
        if column in table.column_names:
            raise CanaryError(
                f"table {name!r} already has a column {column!r}: choose another marker"
            )
        tables[name] = table.append_column(column, pa.array([value] * table.num_rows, pa.string()))
        plan.files.append(str(out / f"{name}.{fmt}"))
    plan.files.append(str(out / "canary.json"))
    if dry_run:
        return plan
    out.mkdir(parents=True, exist_ok=True)
    for name, table in tables.items():
        _write_table(table, out / f"{name}.{fmt}", fmt)
    (out / "canary.json").write_text(json.dumps(plan.document(), indent=2) + "\n", encoding="utf-8")
    return plan


# ---- check ---------------------------------------------------------------------------------------


@dataclass
class Report:
    """The expected detections of a canary against what the results show."""

    canary: str
    present: list[str] = field(default_factory=list)
    blind_spots: list[str] = field(default_factory=list)
    fired: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.blind_spots

    def to_dict(self) -> dict[str, Any]:
        return {
            "canary": self.canary,
            "ok": self.ok,
            "present": self.present,
            "blind_spots": self.blind_spots,
            "fired": self.fired,
        }


def check(canary: dict[str, Any], docs: list[Any], names: list[str] | None = None) -> Report:
    """Compare the expected detections of ``canary`` with the ``shape-result`` documents
    ``docs`` (the ``--json`` output of the user's own checks). Raises
    :class:`~shape.scenario.results.ResultError` for a document that is not one."""
    if not docs:
        raise results.ResultError("give at least one result document (--result)")
    found: set[str] = set()
    for i, doc in enumerate(docs):
        label = names[i] if names else f"result {i + 1}"
        found |= results.fired(doc, label)
    report = Report(str(canary["id"]), fired=sorted(found))
    for item in canary["expected"]:
        (report.present if results.satisfied(item, found) else report.blind_spots).append(item)
    return report


def load_results(paths: list[str]) -> tuple[list[Any], list[str]]:
    docs = []
    for p in paths:
        try:
            docs.append(read_json(Path(p), "the result"))
        except LibraryError as exc:
            raise results.ResultError(str(exc)) from None
    return docs, [Path(p).name for p in paths]
