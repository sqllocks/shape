"""``shape skew``: training-serving skew between two datasets or profiles (W3-11).

Nothing heavy loads at import time (T-18); the command imports Arrow when it runs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

PROJECT_FILE = "shape.yml"


def add_arguments(sub: Any) -> None:
    sk = sub.add_parser(
        "skew",
        help="compare serving data with training data, feature by feature",
        description="Compare SERVING with TRAIN (data, or profiles): per feature the schema "
        "(missing in serving, type changed), the null rate, the population stability index "
        "(as `shape drift --psi`; flagged at 0.2 or more), the share of serving rows with a "
        "category never seen in training, and the share of serving values outside the training "
        "minimum and maximum. Features are ranked by PSI. Exit 0 when nothing is flagged, 1 when "
        "a feature is flagged, 2 for unusable input. A profile holds no values: with one, only "
        "the schema and null-rate skew are reported.",
    )
    sk.add_argument("train", metavar="TRAIN", help="training data (file or folder) or a profile")
    sk.add_argument("serving", metavar="SERVING", help="serving data (file or folder) or a profile")
    sk.add_argument("--format", choices=("auto", "csv", "parquet", "jsonl"), default="auto")
    sk.add_argument(
        "--features", metavar="COLS", help="comma-separated feature columns (default: all)"
    )
    sk.add_argument(
        "--label",
        metavar="COL",
        help="the label: left out of the features; its positive rate is compared",
    )
    sk.add_argument(
        "--slice-by",
        metavar="COL",
        help="repeat the measures for each value of this column (slices under "
        "--threshold min_slice_rows=N rows, default 30, are pooled)",
    )
    sk.add_argument(
        "--threshold",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="a threshold (repeatable): psi (0.2), null_rate (0.05), unseen_category_share "
        "(0.01), out_of_range_share (0.01), label_rate_diff (not set), min_slice_rows (30)",
    )
    sk.add_argument(
        "--project",
        metavar="FILE|DIR",
        help="project file shape.yml (or its folder): thresholds of sources.NAME.thresholds",
    )
    sk.add_argument(
        "--source", metavar="NAME", help="the source of --project (default: the only one)"
    )
    sk.add_argument("--json", action="store_true", help="print the report as JSON")
    sk.add_argument("-o", "--output", metavar="REPORT.json", help="write the JSON report to a file")


def _project_thresholds(path: str, source: str | None) -> dict[str, float | int]:
    from shape.quality.training_skew import DEFAULT_THRESHOLDS, SkewError, _threshold
    from shape.security.yamlsafe import safe_load_yaml

    p = Path(path)
    if p.is_dir():
        p = p / PROJECT_FILE
    if not p.is_file():
        raise FileNotFoundError(f"project file not found: {p}")
    try:
        doc = safe_load_yaml(p.read_text(encoding="utf-8"))
    except ImportError:
        raise SkewError(f"reading {p} needs PyYAML: pip install pyyaml") from None
    sources = doc.get("sources") if isinstance(doc, dict) else None
    if not isinstance(sources, dict) or not sources:
        return {}
    if source is None:
        if len(sources) != 1:
            print(
                f"shape: note: {p} has several sources and none was chosen (--source NAME); "
                "its thresholds are not applied",
                file=sys.stderr,
            )
            return {}
        source = next(iter(sources))
    if source not in sources:
        raise SkewError(f"{p} has no source {source!r} (it has {', '.join(sources)})")
    found = (sources[source] or {}).get("thresholds") or {}
    if not isinstance(found, dict):
        raise SkewError(f"{p}: sources.{source}.thresholds must be a mapping")
    # the file's thresholds also hold drift keys the skew report has no use for
    return {k: _threshold(k, v) for k, v in found.items() if k in DEFAULT_THRESHOLDS}


def run(a: argparse.Namespace) -> int:
    from shape.quality.training_skew import parse_thresholds, skew

    thresholds: dict[str, float | int] = {}
    if a.project:
        thresholds.update(_project_thresholds(a.project, a.source))
    elif a.source:
        raise ValueError("--source needs --project")
    thresholds.update(parse_thresholds(a.threshold))
    features = None
    if a.features is not None:
        features = [c.strip() for c in a.features.split(",")]
        if not all(features):
            raise ValueError(f"--features needs COL[,COL], got {a.features!r}")
    report = skew(
        a.train,
        a.serving,
        features=features,
        label=a.label,
        slice_by=a.slice_by,
        thresholds=thresholds,
        fmt=a.format,
    )
    if a.output:
        Path(a.output).write_text(report.to_json(), encoding="utf-8")
    sys.stdout.write(report.to_json() if a.json else report.to_markdown())
    return 1 if report.flagged else 0
