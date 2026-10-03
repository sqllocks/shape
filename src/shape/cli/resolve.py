"""``shape resolve``: find duplicate entities, build golden records, and plant scored duplicates.

``resolve run`` blocks, matches and clusters the rows of a CSV, Parquet or JSON Lines file and
writes golden records (and, optionally, the clusters and a report); with ``--truth`` it scores the
result (precision, recall, F1). ``resolve synth`` plants seeded duplicates in a clean file and
writes the true clusters. Nothing heavy loads at import time (T-18).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

SUFFIXES = (".csv", ".parquet", ".jsonl")


def add_arguments(sub: Any) -> None:
    """Register ``resolve`` on the subparsers."""
    rs = sub.add_parser(
        "resolve",
        help="find duplicate entities, build golden records, plant scored duplicates",
        description="Entity resolution: blocking, fuzzy matching, clustering and golden records, "
        "scored against known clusters from `resolve synth`. Deterministic: the same input, "
        "options and seed give the same output.",
    )
    rsub = rs.add_subparsers(dest="resolve_cmd", required=True)
    run = rsub.add_parser("run", help="resolve the duplicates of a file")
    run.add_argument("input", help="a .csv, .parquet or .jsonl file")
    run.add_argument("--config", metavar="FILE", help="a shape-resolve-config JSON file")
    run.add_argument(
        "--block",
        action="append",
        metavar="COL[+COL]:METHOD[:SIZE]",
        help="a blocking rule (repeatable); METHOD is key, prefix, phonetic or ngram",
    )
    run.add_argument(
        "--match",
        action="append",
        metavar="COL:KIND[:WEIGHT[:TOLERANCE[:relative]]]",
        help="a matching field (repeatable); KIND is exact, text, levenshtein, jaro_winkler, "
        "ngram, tokens, phonetic, numeric or date (a date tolerance is in days)",
    )
    run.add_argument("--threshold", type=float, help="the match score, above 0 to 1 (default 0.85)")
    run.add_argument("--cluster", choices=("connected", "center"), help="default: connected")
    run.add_argument("--max-block", type=int, help="skip blocks larger than this (default 500)")
    run.add_argument(
        "--survive",
        action="append",
        metavar="COL=RULE[:BY[:A,B,...]]",
        help="a survivorship rule for the golden record (repeatable); RULE is first, last, "
        "most_common, longest, shortest, min, max, most_recent:BYCOL or priority:BYCOL:A,B,...",
    )
    run.add_argument("--truth", metavar="FILE", help="true clusters from `resolve synth`, to score")
    run.add_argument("--golden", metavar="FILE", help="write the golden records here")
    run.add_argument("--clusters", metavar="FILE", help="write the clusters (JSON) here")
    run.add_argument("--report", metavar="FILE", help="write a JSON report here")
    run.add_argument("--json", action="store_true", help="print the result as JSON")
    syn = rsub.add_parser("synth", help="plant seeded duplicates and record the true clusters")
    syn.add_argument("input", help="a clean .csv, .parquet or .jsonl file")
    syn.add_argument(
        "-o", "--output", required=True, metavar="FILE", help="the table with duplicates"
    )
    syn.add_argument("--truth", metavar="FILE", help="write the true clusters (JSON) here")
    syn.add_argument(
        "--rate", type=float, default=0.2, help="share of rows duplicated (default 0.2)"
    )
    syn.add_argument("--max-copies", type=int, default=2, help="most copies of a row (default 2)")
    syn.add_argument(
        "--fuzz", type=float, default=0.5, help="chance a copied cell is damaged (default 0.5)"
    )
    syn.add_argument("--seed", type=int, default=42, help="the seed (default 42)")
    syn.add_argument("--id-column", metavar="COL", help="an integer key; copies get new keys")
    syn.add_argument("--skip", action="append", metavar="COL", help="a column to leave undamaged")
    syn.add_argument("--json", action="store_true", help="print the result as JSON")


def read_table(path: str) -> Any:
    import pyarrow as pa  # type: ignore[import-untyped]

    suffix = Path(path).suffix.lower()
    if suffix not in SUFFIXES:
        raise ValueError(f"{path}: use a {', '.join(SUFFIXES)} file")
    if not Path(path).is_file():
        raise FileNotFoundError(path)
    if suffix == ".csv":
        import pyarrow.csv as pacsv  # type: ignore[import-untyped]

        return pacsv.read_csv(path)
    if suffix == ".parquet":
        import pyarrow.parquet as pq  # type: ignore[import-untyped]

        return pq.read_table(path)
    import pyarrow.json as pajson  # type: ignore[import-untyped]

    from shape.security.jsondepth import check_json_file

    check_json_file(path)
    table = pajson.read_json(path)
    return table if isinstance(table, pa.Table) else table.to_table()


def write_table(table: Any, path: str) -> None:
    suffix = Path(path).suffix.lower()
    if suffix not in SUFFIXES:
        raise ValueError(f"{path}: use a {', '.join(SUFFIXES)} file")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if suffix == ".csv":
        import pyarrow.csv as pacsv

        pacsv.write_csv(table, target)
    elif suffix == ".parquet":
        import pyarrow.parquet as pq

        pq.write_table(table, target)
    else:
        with target.open("w", encoding="utf-8") as f:
            for row in table.to_pylist():
                f.write(json.dumps(row, sort_keys=True, default=str, ensure_ascii=False) + "\n")


def _parse_block(text: str) -> Any:
    from shape.resolve import BlockRule

    parts = text.split(":")
    if len(parts) not in (2, 3) or not parts[0]:
        raise ValueError(f"--block is COL[+COL]:METHOD[:SIZE], got {text!r}")
    cols = tuple(parts[0].split("+"))
    size = _int(parts[2], "--block size") if len(parts) == 3 else None
    return BlockRule(cols[0] if len(cols) == 1 else cols, parts[1], size)


def _int(text: str, what: str) -> int:
    try:
        return int(text)
    except ValueError:
        raise ValueError(f"{what} must be a whole number, got {text!r}") from None


def _float(text: str, what: str) -> float:
    try:
        return float(text)
    except ValueError:
        raise ValueError(f"{what} must be a number, got {text!r}") from None


def _parse_match(text: str) -> Any:
    from shape.resolve import FieldMatch

    parts = text.split(":")
    if not 2 <= len(parts) <= 5 or not parts[0]:
        raise ValueError(f"--match is COL:KIND[:WEIGHT[:TOLERANCE[:relative]]], got {text!r}")
    weight = _float(parts[2], "--match weight") if len(parts) > 2 and parts[2] else 1.0
    tol = _float(parts[3], "--match tolerance") if len(parts) > 3 and parts[3] else None
    relative = False
    if len(parts) == 5:
        if parts[4] != "relative":
            raise ValueError(f"--match: the last part is 'relative' or nothing, got {parts[4]!r}")
        relative = True
    return FieldMatch(parts[0], parts[1], weight, tol, relative)


def _parse_survive(text: str) -> tuple[str, Any]:
    from shape.resolve import Survivorship

    col, eq, rest = text.partition("=")
    if not eq or not col or not rest:
        raise ValueError(f"--survive is COL=RULE[:BY[:A,B,...]], got {text!r}")
    parts = rest.split(":")
    order = tuple(parts[2].split(",")) if len(parts) > 2 else ()
    return col, Survivorship(parts[0], parts[1] if len(parts) > 1 else None, order)


def _config(a: argparse.Namespace) -> Any:
    from shape.resolve import ResolveConfig

    if a.config:
        with open(a.config, encoding="utf-8") as fh:
            base = ResolveConfig.from_dict(json.load(fh))
        blocks, fields = list(base.blocks), list(base.fields)
        threshold, cluster, max_block = base.threshold, base.cluster, base.max_block
        survivorship = dict(base.survivorship)
    else:
        blocks, fields, threshold, cluster, max_block, survivorship = (
            [],
            [],
            0.85,
            "connected",
            500,
            {},
        )
    if a.block:
        blocks = [_parse_block(b) for b in a.block]
    if a.match:
        fields = [_parse_match(m) for m in a.match]
    if not blocks or not fields:
        raise ValueError("give --config, or at least one --block and one --match")
    for item in a.survive or []:
        col, rule = _parse_survive(item)
        survivorship[col] = rule
    return ResolveConfig(
        blocks,
        fields,
        a.threshold if a.threshold is not None else threshold,
        a.cluster or cluster,
        survivorship,
        a.max_block if a.max_block is not None else max_block,
    )


def _dump(path: str, doc: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(doc, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )


def cmd_run(a: argparse.Namespace) -> int:
    from shape.resolve import pair_metrics, read_truth, resolve

    config = _config(a)
    table = read_table(a.input)
    result = resolve(table, config)
    summary: dict[str, Any] = {
        "input": a.input,
        "rows": table.num_rows,
        "clusters": result.stats["clusters"],
        "duplicate_clusters": result.stats["duplicate_clusters"],
        "records_merged": result.stats["records_merged"],
        "candidate_pairs": result.stats["candidate_pairs"],
        "matched_pairs": result.stats["matched_pairs"],
    }
    if a.truth:
        truth = read_truth(a.truth, rows=table.num_rows)
        summary["metrics"] = pair_metrics(result.labels, truth).as_dict()
        summary["blocking_recall"] = result.blocking_recall(truth)
    if a.golden:
        write_table(result.golden.table, a.golden)
        summary["golden"] = a.golden
    if a.clusters:
        _dump(
            a.clusters,
            {
                "format": "shape-resolve-clusters",
                "version": 1,
                "rows": table.num_rows,
                "clusters": result.clusters(),
            },
        )
        summary["cluster_file"] = a.clusters
    if a.report:
        _dump(
            a.report,
            {
                "format": "shape-resolve-report",
                "version": 1,
                "config": config.to_dict(),
                "summary": summary,
                "blocking": result.stats["blocking"],
                "lineage": result.golden.lineage,
            },
        )
    if a.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    print(
        f"{table.num_rows:,} rows -> {summary['clusters']:,} entities "
        f"({summary['records_merged']:,} merged, {summary['matched_pairs']:,} matched pairs "
        f"of {summary['candidate_pairs']:,} candidates)"
    )
    if "metrics" in summary:
        m = summary["metrics"]
        print(
            f"precision {m['precision']:.3f}  recall {m['recall']:.3f}  f1 {m['f1']:.3f}  "
            f"(blocking recall {summary['blocking_recall']:.3f})"
        )
    return 0


def cmd_synth(a: argparse.Namespace) -> int:
    from shape.resolve import make_duplicates, write_truth

    table = read_table(a.input)
    dup = make_duplicates(
        table,
        rate=a.rate,
        max_copies=a.max_copies,
        fuzz=a.fuzz,
        seed=a.seed,
        id_column=a.id_column,
        skip=tuple(a.skip or ()),
    )
    write_table(dup.table, a.output)
    if a.truth:
        write_truth(a.truth, dup)
    summary = {
        "output": a.output,
        "truth": a.truth,
        "seed": a.seed,
        "rows": dup.table.num_rows,
        "clusters": len(dup.clusters),
        "true_pairs": dup.true_pairs,
    }
    if a.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(
            f"Wrote {dup.table.num_rows:,} rows to {a.output} "
            f"({len(dup.clusters):,} duplicate clusters, {dup.true_pairs:,} true pairs)"
        )
    return 0


def run(a: argparse.Namespace) -> int:
    return cmd_run(a) if a.resolve_cmd == "run" else cmd_synth(a)


__all__ = ["add_arguments", "cmd_run", "cmd_synth", "run"]
