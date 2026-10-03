"""``shape verify``: load generated tables, run the validation gates, report.

Without a schema only row counts are reported. With a schema the runner adds schema
conformance, null constraints, primary-key uniqueness and foreign-key integrity; with
``statistical=True`` it adds the distribution gate (KS and chi-squared, needs scipy).
"""

from __future__ import annotations

import json
import warnings
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]

from .gates import (
    DistributionGate,
    FileFormatGate,
    GateResult,
    NullConstraintGate,
    RangeConstraintGate,
    ReferentialIntegrityGate,
    SchemaConformanceGate,
    SchemaDriftGate,
    TemporalConsistencyGate,
    UniqueConstraintGate,
    ValidationContext,
)
from .gatespec import GateSchema
from .memorization import MemorizationGate
from .utility import UtilityGate
from .verifyconfig import VerifyConfig

FORMATS = ("csv", "parquet", "jsonl")
_SUFFIX_FORMAT = {".csv": "csv", ".parquet": "parquet", ".jsonl": "jsonl"}


def _read(path: Path, fmt: str) -> pa.Table:
    from shape.io import PANDAS_CSV, read_table

    if fmt == "parquet":
        import pyarrow.parquet as pq  # type: ignore[import-untyped]

        table: pa.Table = pq.read_table(path)
        return table
    if fmt == "csv":
        return read_table(path, csv=PANDAS_CSV)
    return read_table(path)


def load_tables(path: str | Path, fmt: str = "auto") -> dict[str, pa.Table]:
    """Load a data file, or every data file of a directory, as Arrow tables keyed by file stem.

    ``fmt`` is ``csv``, ``parquet`` or ``jsonl``; ``auto`` uses the file's extension, and for a
    directory loads every parquet, csv and jsonl file in it, each by its own extension. A
    directory with a fixed ``fmt`` loads that format only and warns for each file of another
    supported format that it skips. Raises ``FileNotFoundError`` for a missing path and
    ``ValueError`` for an unsupported format, or for a table present in two formats."""
    if fmt != "auto" and fmt not in FORMATS:
        raise ValueError(f"Unsupported format '{fmt}'. Choose from: {FORMATS}")
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Path not found: {path}")
    if p.is_file():
        use = fmt if fmt != "auto" else _SUFFIX_FORMAT.get(p.suffix.lower())
        if use is None:
            raise ValueError(f"Cannot tell the format of {p.name}; pass a format of {FORMATS}")
        return {p.stem: _read(p, use)}
    chosen = _select(p, fmt)
    return {fp.stem: _read(fp, f) for fp, f in chosen}


def data_files(path: str | Path, fmt: str = "auto") -> list[Path]:
    """The files :func:`load_tables` reads for ``path`` and ``fmt``."""
    p = Path(path)
    if p.is_file():
        return [p]
    return [fp for fp, _ in _select(p, fmt)]


def _select(p: Path, fmt: str) -> list[tuple[Path, str]]:
    # every data file directly in the directory, by its extension in any case (``B.CSV`` too);
    # a sub-directory is never a table, whatever its name
    if not p.is_dir():
        return []
    found = [
        (fp, _SUFFIX_FORMAT[fp.suffix.lower()])
        for fp in p.iterdir()
        if fp.suffix.lower() in _SUFFIX_FORMAT and fp.is_file()
    ]
    chosen = [(fp, f) for fp, f in found if fmt in ("auto", f)]
    chosen.sort(key=lambda item: (item[0].stem, item[0].name))
    if fmt != "auto":
        skipped = sorted(fp.name for fp, f in found if f != fmt)
        for name in skipped:
            warnings.warn(
                f"skipped {name}: format {(kind := _SUFFIX_FORMAT[Path(name).suffix.lower()])} "
                f"differs from the requested {fmt}; use --format {kind}, or --format auto "
                "to load every file",
                UserWarning,
                stacklevel=2,
            )
    by_stem: dict[str, Path] = {}
    for fp, _ in chosen:
        if fp.stem in by_stem:
            raise ValueError(
                f"table '{fp.stem}' is in both {by_stem[fp.stem].name} and {fp.name}; "
                "keep one of them or pass --format to pick a format"
            )
        by_stem[fp.stem] = fp
    return chosen


@dataclass
class VerifyResult:
    """Outcome of one verify run."""

    passed: bool
    gate_results: list[GateResult]
    row_counts: dict[str, int]
    run_at: str
    data_path: str
    schema_path: str | None
    statistical: bool
    shape_version: str
    config_path: str | None = None
    source_path: str | None = None
    distribution_alpha: float = 0.05


class VerifyRunner:
    """Run the curated gate set against tables (see the module docstring)."""

    def __init__(
        self,
        schema: GateSchema | None = None,
        statistical: bool = False,
        data_path: str = "",
        schema_path: str | None = None,
        config: VerifyConfig | None = None,
        config_path: str | None = None,
        files: list[Path] | None = None,
        *,
        source: dict[str, pa.Table] | None = None,
        source_path: str | None = None,
    ) -> None:
        self._source = source or {}
        self._source_path = source_path
        self._config = config
        self._config_path = config_path
        self._files = files or []
        self._schema = schema
        self._statistical = statistical
        self._data_path = data_path
        self._schema_path = schema_path

    def run(self, tables: dict[str, pa.Table]) -> VerifyResult:
        from shape import __version__

        cfg = self._config
        ctx = ValidationContext(
            tables=tables,
            schema=self._schema,
            file_paths=[Path(f) for f in cfg.file_paths] if cfg else [],
            config=dict(cfg.rules) if cfg else {},
            source_tables=self._source,
        )
        if cfg and cfg.check_data_files:
            ctx.file_paths.extend(self._files)
        results: list[GateResult] = []
        if self._schema is not None:
            results.append(SchemaConformanceGate().check(ctx))
            results.append(NullConstraintGate().check(ctx))
            results.append(UniqueConstraintGate().check(ctx))
            results.append(ReferentialIntegrityGate().check(ctx))
        if cfg:
            if "ranges" in cfg.rules:
                results.append(RangeConstraintGate().check(ctx))
            if cfg.temporal:
                results.append(TemporalConsistencyGate().check(ctx))
            if "baseline" in cfg.rules:
                results.append(SchemaDriftGate().check(ctx))
            if ctx.file_paths:
                results.append(FileFormatGate().check(ctx))
        if self._statistical:
            results.append(DistributionGate().check(ctx))
        if self._source:
            results.append(MemorizationGate().check(ctx))
            if "utility" in ctx.config:
                results.append(UtilityGate().check(ctx))
        return VerifyResult(
            passed=all(r.passed for r in results),
            gate_results=results,
            row_counts={name: t.num_rows for name, t in tables.items()},
            run_at=datetime.now(UTC).isoformat(),
            data_path=self._data_path,
            schema_path=self._schema_path,
            statistical=self._statistical,
            shape_version=__version__,
            config_path=self._config_path,
            source_path=self._source_path,
            distribution_alpha=float(ctx.config.get("distribution_alpha", 0.05)),
        )


_GATE_DESCRIPTIONS = {
    "schema_conformance": "Column names and types validated against schema declarations.",
    "null_constraint": "Non-nullable columns checked for null values.",
    "unique_constraint": "Primary key columns checked for duplicate values.",
    "referential_integrity": "FK column values verified against parent PK sets.",
    "range_constraint": "Numeric columns checked against the configured minimum and maximum.",
    "temporal_consistency": (
        "Datetime columns checked against the configured date range, for future dates and "
        "for start/end ordering."
    ),
    "schema_drift": "Tables and column types compared with the configured baseline.",
    "file_format": "Data files checked to exist, be non-empty and read in full.",
    "memorization": (
        "Generated rows compared with the source rows: exact matches in columns classified "
        "CONFIDENTIAL or above fail; nearest-neighbour distance is reported."
    ),
    "utility": (
        "A model trained on the generated data is tested on held-out real data and compared "
        "with a model trained on real data; fails below the minimum retention."
    ),
    "distribution": (
        "KS test (numeric) and chi-squared test (enum) comparing observed "
        "distributions to schema-declared parameters (α={alpha:g})."
    ),
}


class VerifyReport:
    """A :class:`VerifyResult` as JSON or Markdown."""

    def __init__(self, result: VerifyResult) -> None:
        self._r = result

    def to_json(self, indent: int = 2) -> str:
        r = self._r
        payload = {
            "shape_version": r.shape_version,
            "run_at": r.run_at,
            "data_path": r.data_path,
            "schema_path": r.schema_path,
            "config_path": r.config_path,
            "source_path": r.source_path,
            "statistical": r.statistical,
            "passed": r.passed,
            "row_counts": r.row_counts,
            "gates": [
                {
                    "gate": g.gate_name,
                    "passed": g.passed,
                    "errors": g.errors,
                    "warnings": g.warnings,
                    "details": g.details,
                }
                for g in r.gate_results
            ],
        }
        return json.dumps(payload, indent=indent, default=str)

    def to_markdown(self) -> str:
        r = self._r
        lines = [
            "# Shape Verify Report",
            "",
            f"**Generated:** {r.run_at}  ",
            f"**Data path:** {r.data_path}  ",
            f"**Schema:** {r.schema_path or '(none)'}  ",
            f"**Config:** {r.config_path or '(none)'}  ",
            f"**Source:** {r.source_path or '(none)'}  ",
            f"**Statistical tests:** {'Yes' if r.statistical else 'No'}  ",
            f"**Shape version:** {r.shape_version}  ",
            "",
            f"**Overall: {'PASS' if r.passed else 'FAIL'}**",
            "",
            "---",
            "",
            "## Summary",
            "",
            "| Gate | Status | Errors | Warnings |",
            "|------|--------|--------|----------|",
        ]
        for g in r.gate_results:
            status = "PASS" if g.passed else "FAIL"
            icon = "✅" if g.passed else "❌"
            lines.append(
                f"| {g.gate_name} | {icon} {status} | {len(g.errors)} | {len(g.warnings)} |"
            )
        total_errors = sum(len(g.errors) for g in r.gate_results)
        total_warnings = sum(len(g.warnings) for g in r.gate_results)
        lines += [
            "",
            f"**Total:** {len(r.gate_results)} gates — {total_errors} errors, "
            f"{total_warnings} warnings",
        ]
        if r.row_counts:
            lines += ["", "## Row Counts", "", "| Table | Rows |", "|-------|------|"]
            lines += [f"| {t} | {n:,} |" for t, n in sorted(r.row_counts.items())]
        if any(g.errors or g.warnings for g in r.gate_results):
            lines += ["", "## Gate Details", ""]
            for g in r.gate_results:
                if not g.errors and not g.warnings:
                    continue
                lines.append(f"### {g.gate_name} — {'PASS' if g.passed else 'FAIL'}")
                lines.append("")
                lines += [f"- **ERROR:** {e}" for e in g.errors]
                lines += [f"- **WARN:** {w}" for w in g.warnings]
                lines.append("")
        reproduce = f"shape verify {r.data_path}"
        if r.schema_path:
            reproduce += f" --schema {r.schema_path}"
        if r.config_path:
            reproduce += f" --config {r.config_path}"
        if r.source_path:
            reproduce += f" --source {r.source_path}"
        if r.statistical:
            reproduce += " --statistical"
        lines += [
            "## Methodology",
            "",
            f"This report was generated by `shape verify` v{r.shape_version}.",
            "Gates applied:",
        ]
        lines += [
            f"- **{g.gate_name}:** "
            + _GATE_DESCRIPTIONS.get(g.gate_name, "Custom gate.").replace(
                "{alpha:g}", format(r.distribution_alpha, "g")
            )
            for g in r.gate_results
        ]
        lines += ["", "**Reproduce this report:**", "", "```", reproduce, "```"]
        return "\n".join(lines) + "\n"
