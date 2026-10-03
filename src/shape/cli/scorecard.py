"""``shape scorecard``: quality scores by dimension, failing-row samples, a flag column (W3-06).

Nothing heavy loads at import time (T-18); the command imports Arrow and the gates when it runs.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any


def add_arguments(sub: Any) -> None:
    sc = sub.add_parser(
        "scorecard",
        help="score data quality by dimension, with failing-row samples",
        description="Run the validation gates over data and score six dimensions (accuracy, "
        "completeness, conformity, consistency, timeliness, uniqueness) from them, with the "
        "owner of each failing column, the trend over earlier scorecards, and failing-row "
        "samples that never show the values of classified columns unless asked. With --slice-by "
        "every dimension is also scored per slice. Exit 0 when the scorecard was produced (and, "
        "with --max-slice-gap, no slice gap is above it), 1 when a slice gap is above "
        "--max-slice-gap, 2 on an input error.",
    )
    sc.add_argument("data", metavar="DATA", help="a data file, or a directory of data files")
    sc.add_argument("--format", choices=("auto", "csv", "parquet", "jsonl"), default="auto")
    sc.add_argument("--schema", metavar="GATES.json", help="gate schema (or Shape model v2)")
    sc.add_argument("--config", metavar="CONFIG.json", help="verify configuration (see verify)")
    sc.add_argument(
        "--suppressions",
        metavar="FILE.json",
        help="known issues to snooze or suppress (format shape-scorecard-suppressions)",
    )
    sc.add_argument(
        "--project",
        metavar="DIR",
        help="directory of the project file shape.yml, for column owners (default: here)",
    )
    sc.add_argument(
        "--history", metavar="DIR", help="registry directory holding earlier scorecards"
    )
    sc.add_argument("--name", metavar="NAME", help="name of the scorecard series in --history")
    sc.add_argument("--record", action="store_true", help="add this scorecard to --history")
    sc.add_argument(
        "--samples",
        type=int,
        default=5,
        metavar="N",
        help="failing rows to show per failing check (default 5; 0 for none)",
    )
    sc.add_argument(
        "--classified",
        action="append",
        default=[],
        metavar="TABLE.COLUMN",
        help="treat this column as classified: samples show [redacted] for it",
    )
    sc.add_argument(
        "--show-classified",
        action="store_true",
        help="show the values of classified columns in samples (off by default)",
    )
    sc.add_argument(
        "--flag-output",
        metavar="DIR",
        help="write each table, with a boolean flag column marking its failing rows, to DIR",
    )
    sc.add_argument(
        "--flag-column",
        default="_shape_dq_failed",
        metavar="NAME",
        help="name of the flag column (default _shape_dq_failed)",
    )
    sc.add_argument(
        "--slice-by",
        metavar="COLUMN[,COLUMN]",
        help="also score every dimension per slice: each value (or value combination) of these "
        "columns in the tables that hold them; null is the slice (null)",
    )
    sc.add_argument(
        "--min-slice-rows",
        type=int,
        default=30,
        metavar="N",
        help="slices with fewer rows are pooled as (small slices) and never shown alone "
        "(default 30)",
    )
    sc.add_argument(
        "--max-slice-gap",
        type=float,
        metavar="G",
        help="exit 1 when any dimension's gap (highest minus lowest slice score) exceeds G",
    )
    sc.add_argument(
        "--reference",
        metavar="REF",
        help="with --slice-by: data or a profile of the population the data should describe; "
        "reports each slice's reference share and the ratio",
    )
    sc.add_argument(
        "--label",
        metavar="COLUMN",
        help="with --slice-by: a boolean or two-valued column; reports each slice's positive "
        "rate and the disparity ratio (flagged below 0.8, the four-fifths screening heuristic)",
    )
    sc.add_argument("--json", action="store_true", help="print the scorecard as JSON")
    sc.add_argument("-o", "--output", metavar="FILE", help="write the scorecard to FILE")


def _classified(pairs: list[str]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for pair in pairs:
        table, sep, column = pair.partition(".")
        if not sep or not table or not column:
            raise ValueError(f"--classified needs TABLE.COLUMN, got {pair!r}")
        out.setdefault(table, set()).add(column)
    return out


def _slice_columns(text: str) -> list[str]:
    columns = [c.strip() for c in text.split(",")]
    if not all(columns):
        raise ValueError(f"--slice-by needs COLUMN[,COLUMN], got {text!r}")
    return columns


def _write_flagged(a: argparse.Namespace, card: Any, tables: dict[str, Any]) -> None:
    import pyarrow.csv as pacsv  # type: ignore[import-untyped]
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    from shape.quality.rowlevel import flag_failing_rows
    from shape.quality.verify import data_files

    files = {p.stem: p for p in data_files(a.data, a.format)}
    out_dir = Path(a.flag_output)
    for p in files.values():
        if (out_dir / p.name).resolve() == p.resolve():
            raise ValueError("the flag output directory would overwrite the input files")
    flagged = {
        name: flag_failing_rows(t, card.outcomes, name, a.flag_column) for name, t in tables.items()
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, t in flagged.items():
        suffix = files[name].suffix.lower() if name in files else ".parquet"
        target = out_dir / f"{name}{suffix}"
        if suffix == ".csv":
            pacsv.write_csv(t, target)
        elif suffix == ".jsonl":
            import json

            with open(target, "w", encoding="utf-8") as fh:
                for row in t.to_pylist():
                    fh.write(json.dumps(row, default=str) + "\n")
        else:
            pq.write_table(t, target)


def run(a: argparse.Namespace) -> int:
    from shape.quality import (
        VerifyRunner,
        load_gate_schema,
        load_tables,
        load_verify_config,
    )
    from shape.quality.scorecard import (
        build_scorecard,
        column_owners,
        load_suppressions,
        record_scorecard,
        scorecard_trend,
    )
    from shape.quality.sources import load_data_or_profile
    from shape.quality.verify import data_files
    from shape.registry.local import LocalRegistry

    if a.record and not (a.history and a.name):
        raise ValueError("--record needs --history DIR and --name NAME")
    if a.name and not a.history:
        raise ValueError("--name needs --history DIR")
    slice_by = _slice_columns(a.slice_by) if a.slice_by is not None else None
    if slice_by is None and (a.reference or a.label or a.max_slice_gap is not None):
        raise ValueError("--reference, --label and --max-slice-gap need --slice-by")
    classified = _classified(a.classified)
    tables = load_tables(a.data, a.format)
    if not tables:
        raise ValueError(f"no {a.format} data files found in {a.data}")
    schema = load_gate_schema(a.schema) if a.schema else None
    config = load_verify_config(a.config) if a.config else None
    suppressions = load_suppressions(a.suppressions) if a.suppressions else []
    registry = LocalRegistry(a.history) if a.history and a.name else None
    history = scorecard_trend(registry, a.name) if registry is not None else None
    result = VerifyRunner(
        schema, False, a.data, a.schema, config, a.config, data_files(a.data, a.format)
    ).run(tables)
    card = build_scorecard(
        result,
        tables,
        schema=schema,
        config=config,
        suppressions=suppressions,
        owners=column_owners(a.project),
        samples=a.samples,
        classified=classified,
        show_classified=a.show_classified,
        history=history,
        slice_by=slice_by,
        min_slice_rows=a.min_slice_rows,
        label=a.label,
        reference=load_data_or_profile(a.reference, a.format) if a.reference else None,
        max_slice_gap=a.max_slice_gap,
    )
    if a.flag_output:
        _write_flagged(a, card, tables)
    if registry is not None and a.record:
        record_scorecard(registry, a.name, card)
    text = card.to_json() if a.json else card.to_markdown()
    if a.output:
        Path(a.output).write_text(text, encoding="utf-8")
    else:
        print(text, end="" if text.endswith("\n") else "\n")
    return 1 if card.slices and card.slices.get("exceeded") else 0
