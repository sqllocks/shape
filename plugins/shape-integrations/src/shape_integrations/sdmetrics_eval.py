"""SDMetrics quality report (imports SDMetrics: needs the ``sdmetrics`` extra).

Runs ``sdmetrics.reports.QualityReport`` over the tables, with a metadata inferred from the
Arrow types (no relationships: Shape does not hand over keys here). Columns of a type SDMetrics
has no ``sdtype`` for (lists, structs, binary) are left out and listed in ``skipped_columns``.
"""

from __future__ import annotations

import warnings
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from .evaluate import EvaluateInputError
from .extras import require

SHAPE_API = "1.0"


def sdtype(t: pa.DataType) -> str | None:
    if pa.types.is_boolean(t):
        return "boolean"
    if pa.types.is_integer(t) or pa.types.is_floating(t) or pa.types.is_decimal(t):
        return "numerical"
    if pa.types.is_timestamp(t) or pa.types.is_date(t):
        return "datetime"
    if pa.types.is_string(t) or pa.types.is_large_string(t):
        return "categorical"
    if pa.types.is_dictionary(t):
        return sdtype(t.value_type)
    return None


def _frame(table: pa.Table, keep: list[str]) -> Any:
    return table.select(keep).to_pandas()


def _rows(details: Any, mapping: dict[str, str]) -> list[dict[str, Any]]:
    out = []
    for rec in details.to_dict(orient="records"):
        out.append({new: rec.get(old) for new, old in mapping.items() if old in rec})
    return out


def run(real: dict[str, pa.Table], synth: dict[str, pa.Table]) -> tuple[str, dict[str, Any]]:
    mod = require("sdmetrics", "sdmetrics", name="SDMetrics")
    tool_version = str(getattr(mod, "__version__", "unknown"))
    columns: dict[str, dict[str, dict[str, str]]] = {}
    skipped: dict[str, list[str]] = {}
    for name, table in real.items():
        if table.num_rows == 0 or synth[name].num_rows == 0:
            raise EvaluateInputError(f"table {name!r} has no rows in the real or synthetic data")
        cols = {}
        for f in table.schema:
            kind = sdtype(f.type)
            if kind is None:
                skipped.setdefault(name, []).append(f.name)
            else:
                cols[f.name] = {"sdtype": kind}
        if not cols:
            raise EvaluateInputError(f"table {name!r} has no column SDMetrics can evaluate")
        columns[name] = cols
    metadata = {"tables": {n: {"columns": c} for n, c in columns.items()}, "relationships": []}
    real_frames = {n: _frame(real[n], list(c)) for n, c in columns.items()}
    synth_frames = {n: _frame(synth[n], list(c)) for n, c in columns.items()}
    from sdmetrics.reports import QualityReport

    report = QualityReport()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            report.generate(real_frames, synth_frames, metadata, verbose=False)
        except (ValueError, KeyError, TypeError) as exc:
            raise EvaluateInputError(f"SDMetrics could not evaluate these tables: {exc}") from None
        properties = report.get_properties()
        shapes = report.get_details("Column Shapes")
        pairs = report.get_details("Column Pair Trends")
    results: dict[str, Any] = {
        "tables": sorted(columns),
        "overall_score": float(report.get_score()),
        "properties": {str(r["Property"]): r["Score"] for r in properties.to_dict("records")},
        "column_shapes": _rows(
            shapes, {"table": "Table", "column": "Column", "metric": "Metric", "score": "Score"}
        ),
        "column_pair_trends": _rows(
            pairs,
            {
                "table": "Table",
                "column_1": "Column 1",
                "column_2": "Column 2",
                "metric": "Metric",
                "score": "Score",
            },
        ),
        "skipped_columns": {k: sorted(v) for k, v in sorted(skipped.items())},
    }
    return tool_version, results


def summary(results: dict[str, Any]) -> list[str]:
    lines = [f"SDMetrics quality score: {_fmt(results.get('overall_score'))}"]
    for name, score in results.get("properties", {}).items():
        lines.append(f"  {name}: {_fmt(score)}")
    return lines


def _fmt(score: Any) -> str:
    return "n/a" if score is None else f"{score:.3f}"
