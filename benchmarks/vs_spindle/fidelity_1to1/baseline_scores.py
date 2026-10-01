"""The baseline's FidelityComparator on one pair of datasets, as JSON (run with ``$SPINDLE_PY``).

    "$SPINDLE_PY" baseline_scores.py REAL_DIR SYNTH_DIR [--tables a,b]

Both directories hold one ``<table>.parquet`` per table, loaded with ``pandas.read_parquet`` exactly
as ``domain_1to1/verify.py`` loads its runs. Prints one JSON object on stdout: the overall score,
and per table the score, the row counts and every column's score and metrics, unrounded. The
checkout is only read.
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path


def load(path: Path, tables: list[str] | None):
    import pandas as pd

    names = tables or sorted(p.stem for p in path.glob("*.parquet"))
    return {t: pd.read_parquet(path / f"{t}.parquet") for t in names}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("real")
    ap.add_argument("synth")
    ap.add_argument("--tables", help="comma-separated table names (default: every file)")
    a = ap.parse_args(argv)
    warnings.simplefilter("ignore")
    from sqllocks_spindle.inference.comparator import FidelityComparator

    only = a.tables.split(",") if a.tables else None
    real, synth = load(Path(a.real), only), load(Path(a.synth), only)
    rep = FidelityComparator().compare(real, synth)
    out = {
        "overall_score": rep.overall_score,
        "tables": {
            t: {
                "score": tf.score,
                "row_count_real": tf.row_count_real,
                "row_count_synth": tf.row_count_synth,
                "columns": {
                    c: {
                        "score": cf.score,
                        "dtype_match": cf.dtype_match,
                        "null_rate_delta": cf.null_rate_delta,
                        "cardinality_ratio": cf.cardinality_ratio,
                        "ks_statistic": cf.ks_statistic,
                        "chi2_statistic": cf.chi2_statistic,
                        "value_overlap": cf.value_overlap,
                    }
                    for c, cf in tf.columns.items()
                },
            }
            for t, tf in rep.tables.items()
        },
    }
    json.dump(out, sys.stdout, allow_nan=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
