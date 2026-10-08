"""``shape evaluate sdmetrics|anonymeter``: informational reports about synthetic data.

::

    shape evaluate sdmetrics REAL_DIR SYNTH_DIR [--tables a,b] [-o REPORT.json] [--json]
    shape evaluate anonymeter REAL_DIR SYNTH_DIR --control CONTROL_DIR
                              [--attacks singling-out,linkability,inference]
                              [-o REPORT.json] [--json]

Tables are read from the directories as ``shape verify`` reads them (Parquet, CSV and JSONL,
one table per file, named by the file stem). The report is a ``shape-evaluation`` document.
Both commands are informational: they exit 0 when the evaluation ran and 2 on bad input (a
missing directory or table, columns that differ, too few rows, a missing extra). They are not
gates; no Shape gate or threshold reads them.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from . import evaluation
from .extras import MissingExtraError

SHAPE_API = "1.0"

EXIT_OK = 0
EXIT_INPUT = 2

ATTACKS = ("singling-out", "linkability", "inference")


class EvaluateInputError(ValueError):
    """The directories, tables or options given to ``shape evaluate`` cannot be evaluated."""


def load_dir(label: str, path: str) -> dict[str, pa.Table]:
    """The tables of directory ``path`` (``label`` names it in messages)."""
    from shape.quality.verify import load_tables

    p = Path(path)
    if not p.is_dir():
        raise EvaluateInputError(f"{label} directory not found: {p}")
    try:
        tables = load_tables(p)
    except (OSError, ValueError, pa.ArrowException) as exc:
        raise EvaluateInputError(f"{label} {p}: {exc}") from None
    if not tables:
        raise EvaluateInputError(f"{label} directory has no Parquet, CSV or JSONL tables: {p}")
    return tables


def split_names(text: str | None, what: str) -> list[str] | None:
    if text is None:
        return None
    names = [n.strip() for n in text.split(",")]
    if not all(names):
        raise EvaluateInputError(f"{what} must be a comma-separated list without empty names")
    return names


def select_tables(
    real: dict[str, pa.Table], others: dict[str, dict[str, pa.Table]], wanted: list[str] | None
) -> list[str]:
    """The tables to evaluate: ``wanted``, or every table of ``real``. Each must exist in every
    directory of ``others`` with the same column names."""
    names = wanted if wanted is not None else sorted(real)
    if len(set(names)) != len(names):
        raise EvaluateInputError("--tables names a table twice")
    for name in names:
        if name not in real:
            raise EvaluateInputError(
                f"table {name!r} is not in the real directory (has: {', '.join(sorted(real))})"
            )
        for label, tables in others.items():
            if name not in tables:
                raise EvaluateInputError(f"table {name!r} is not in the {label} directory")
            if tables[name].column_names != real[name].column_names and set(
                tables[name].column_names
            ) != set(real[name].column_names):
                raise EvaluateInputError(
                    f"table {name!r}: the {label} columns differ from the real columns"
                )
    return list(names)


def _emit(args: Any, report: dict[str, Any], summary: list[str]) -> None:
    text = evaluation.to_json(report)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    if args.json:
        sys.stdout.write(text)
    elif not args.output:
        print("\n".join(summary))
    else:
        print(f"wrote {args.output}")


def _fail(message: str) -> int:
    print(f"shape: error: {message}", file=sys.stderr)
    return EXIT_INPUT


def _common(p: Any) -> None:
    p.add_argument("real", metavar="REAL_DIR", help="directory of the real tables")
    p.add_argument("synth", metavar="SYNTH_DIR", help="directory of the synthetic tables")
    p.add_argument("-o", "--output", metavar="REPORT.json", help="write the report here")
    p.add_argument("--json", action="store_true", help="print the report as JSON")


class EvaluateCommand:
    name = "evaluate"
    help = "Evaluate synthetic data with SDMetrics or Anonymeter (informational reports)"

    def configure(self, parser: Any) -> None:
        sub = parser.add_subparsers(dest="tool", required=True)
        sd = sub.add_parser("sdmetrics", help="SDMetrics quality report", allow_abbrev=False)
        _common(sd)
        sd.add_argument("--tables", metavar="a,b", help="tables to evaluate (default: all)")
        an = sub.add_parser("anonymeter", help="Anonymeter privacy-risk report", allow_abbrev=False)
        _common(an)
        an.add_argument(
            "--control", required=True, metavar="CONTROL_DIR", help="real rows held out of training"
        )
        an.add_argument(
            "--attacks",
            metavar="a,b",
            help=f"attacks to run, from {', '.join(ATTACKS)} (default: all)",
        )

    def run(self, args: Any) -> int:
        try:
            if args.tool == "sdmetrics":
                return self._sdmetrics(args)
            return self._anonymeter(args)
        except MissingExtraError as exc:
            return _fail(str(exc))
        except (EvaluateInputError, evaluation.EvaluationReportError) as exc:
            return _fail(str(exc))

    def _sdmetrics(self, args: Any) -> int:
        real = load_dir("real", args.real)
        synth = load_dir("synthetic", args.synth)
        tables = select_tables(real, {"synthetic": synth}, split_names(args.tables, "--tables"))
        from . import sdmetrics_eval

        version, results = sdmetrics_eval.run(
            {t: real[t] for t in tables}, {t: synth[t] for t in tables}
        )
        report = evaluation.make_report("sdmetrics", version, results)
        _emit(args, report, sdmetrics_eval.summary(report["results"]))
        return EXIT_OK

    def _anonymeter(self, args: Any) -> int:
        attacks = split_names(args.attacks, "--attacks") or list(ATTACKS)
        unknown = [a for a in attacks if a not in ATTACKS]
        if unknown:
            raise EvaluateInputError(
                f"unknown attack {unknown[0]!r}; choose from {', '.join(ATTACKS)}"
            )
        if len(set(attacks)) != len(attacks):
            raise EvaluateInputError("--attacks names an attack twice")
        real = load_dir("real", args.real)
        synth = load_dir("synthetic", args.synth)
        control = load_dir("control", args.control)
        tables = select_tables(real, {"synthetic": synth, "control": control}, None)
        from . import anonymeter_eval

        version, results = anonymeter_eval.run(
            {t: real[t] for t in tables},
            {t: synth[t] for t in tables},
            {t: control[t] for t in tables},
            attacks,
        )
        report = evaluation.make_report("anonymeter", version, results)
        _emit(args, report, anonymeter_eval.summary(report["results"]))
        return EXIT_OK
