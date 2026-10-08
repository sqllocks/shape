"""The pinned baseline's ``learn``: profile, build a schema, dump it as JSON.

Run in the *baseline* venv (internal harness; never part of the product):

    source scripts/env.sh
    "$REFENGINE_PY" benchmarks/vs_refengine/learn_1to1/baseline_learn.py INPUT -o OUT.json \
        [--domain NAME]

``INPUT`` is a data file or a directory of CSV files, read the way the baseline's ``learn`` command
reads them (pandas, one table per file). The output is the command's own schema dictionary
(``cli._schema_to_dict``) plus the correlated column pairs of the builder, which the command drops.
Nothing under the checkout is modified.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
import _refpkg  # noqa: E402


def learn(input_path: Path, domain: str = "inferred") -> dict:
    import pandas as pd

    _schema_to_dict = _refpkg.mod("cli")._schema_to_dict
    DataProfiler = _refpkg.mod("inference").DataProfiler
    SchemaBuilder = _refpkg.mod("inference").SchemaBuilder

    if input_path.is_dir():
        files = sorted(input_path.glob("*.csv"))
    else:
        files = [input_path]
    tables = {fp.stem: pd.read_csv(fp) for fp in files}
    profile = DataProfiler().profile_dataset(tables)
    schema = SchemaBuilder().build(profile, domain_name=domain)
    doc = _schema_to_dict(schema)
    doc["correlated_columns"] = (
        {t: [[a, b, r] for a, b, r in pairs] for t, pairs in schema.correlated_columns.items()}
        if getattr(schema, "correlated_columns", None)
        else {}
    )
    return doc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--domain", default="inferred")
    a = ap.parse_args(argv)
    doc = learn(Path(a.input), a.domain)
    Path(a.output).write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
