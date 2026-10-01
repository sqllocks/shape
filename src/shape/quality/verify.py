"""``shape verify``: load generated tables, run the validation gates, report.

Without a schema only row counts are reported. With a schema the runner adds schema
conformance, null constraints, primary-key uniqueness and foreign-key integrity; with
``statistical=True`` it adds the distribution gate (KS and chi-squared, needs scipy).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]

from .gates import (
    DistributionGate,
    GateResult,
    NullConstraintGate,
    ReferentialIntegrityGate,
    SchemaConformanceGate,
    UniqueConstraintGate,
    ValidationContext,
)
from .gatespec import GateSchema

FORMATS = ("csv", "parquet", "jsonl")
_GLOBS = {"csv": "*.csv", "parquet": "*.parquet", "jsonl": "*.jsonl"}
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
    directory the first of parquet, csv, jsonl that it holds. Raises ``FileNotFoundError`` for
    a missing path and ``ValueError`` for an unsupported format."""
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
    if fmt == "auto":
        fmt = next((f for f in ("parquet", "csv", "jsonl") if any(p.glob(_GLOBS[f]))), "parquet")
    return {fp.stem: _read(fp, fmt) for fp in sorted(p.glob(_GLOBS[fmt]))}


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


class VerifyRunner:
    """Run the curated gate set against tables (see the module docstring)."""

    def __init__(
        self,
        schema: GateSchema | None = None,
        statistical: bool = False,
        data_path: str = "",
        schema_path: str | None = None,
    ) -> None:
        self._schema = schema
        self._statistical = statistical
        self._data_path = data_path
        self._schema_path = schema_path

    def run(self, tables: dict[str, pa.Table]) -> VerifyResult:
        from shape import __version__

        ctx = ValidationContext(tables=tables, schema=self._schema)
        results: list[GateResult] = []
        if self._schema is not None:
            results.append(SchemaConformanceGate().check(ctx))
            results.append(NullConstraintGate().check(ctx))
            results.append(UniqueConstraintGate().check(ctx))
            results.append(ReferentialIntegrityGate().check(ctx))
        if self._statistical:
            results.append(DistributionGate().check(ctx))
        return VerifyResult(
            passed=all(r.passed for r in results),
            gate_results=results,
            row_counts={name: t.num_rows for name, t in tables.items()},
            run_at=datetime.now(UTC).isoformat(),
            data_path=self._data_path,
            schema_path=self._schema_path,
            statistical=self._statistical,
            shape_version=__version__,
        )


_GATE_DESCRIPTIONS = {
    "schema_conformance": "Column names and types validated against schema declarations.",
    "null_constraint": "Non-nullable columns checked for null values.",
    "unique_constraint": "Primary key columns checked for duplicate values.",
    "referential_integrity": "FK column values verified against parent PK sets.",
    "distribution": (
        "KS test (numeric) and chi-squared test (enum) comparing observed "
        "distributions to schema-declared parameters (α=0.05)."
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
        if r.statistical:
            reproduce += " --statistical"
        lines += [
            "## Methodology",
            "",
            f"This report was generated by `shape verify` v{r.shape_version}.",
            "Gates applied:",
        ]
        lines += [
            f"- **{g.gate_name}:** {_GATE_DESCRIPTIONS.get(g.gate_name, 'Custom gate.')}"
            for g in r.gate_results
        ]
        lines += ["", "**Reproduce this report:**", "", "```", reproduce, "```"]
        return "\n".join(lines) + "\n"
