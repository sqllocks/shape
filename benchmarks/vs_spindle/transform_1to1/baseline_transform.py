"""The pinned baseline's star-schema and CDM transforms on a directory of tables.

Run in the *baseline* venv (internal harness; never part of the product):

    source scripts/env.sh
    "$SPINDLE_PY" benchmarks/vs_spindle/transform_1to1/baseline_transform.py \
        --input DIR --out OUT [--format parquet|csv]
    "$SPINDLE_PY" benchmarks/vs_spindle/transform_1to1/baseline_transform.py \
        --generate retail --scale small --seed 42 --input DIR --out OUT

``--input`` holds one Parquet or CSV file per table (read with pandas). With ``--generate`` the
baseline's own generator first writes the retail tables there. The transforms are run exactly as
the baseline's ``to-star`` and ``to-cdm`` commands run them (same classes, same writers, the
domain's own maps); ``OUT/star`` and ``OUT/cdm`` get the result. The checkout is only read.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _read(root: Path) -> dict:
    import pandas as pd

    tables = {}
    for fp in sorted(root.iterdir()):
        if fp.suffix == ".parquet":
            tables[fp.stem] = pd.read_parquet(fp)
        elif fp.suffix == ".csv":
            tables[fp.stem] = pd.read_csv(fp)
    return tables


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--format", default="parquet", choices=("parquet", "csv"))
    ap.add_argument("--generate", metavar="DOMAIN")
    ap.add_argument("--scale", default="small")
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args(argv)

    from sqllocks_spindle.domains.retail import RetailDomain
    from sqllocks_spindle.output import PandasWriter
    from sqllocks_spindle.transform import CdmMapper, StarSchemaTransform

    if a.generate:
        from sqllocks_spindle.engine.generator import Spindle

        if a.generate != "retail":
            raise SystemExit("only retail is wired in this harness")
        result = Spindle().generate(domain=RetailDomain(), scale=a.scale, seed=a.seed)
        a.input.mkdir(parents=True, exist_ok=True)
        PandasWriter().to_parquet(result.tables, a.input)
    tables = _read(a.input)
    domain = RetailDomain()
    star = StarSchemaTransform().transform(tables, domain.star_schema_map())
    writer = PandasWriter()
    star_dir, cdm_dir = a.out / "star", a.out / "cdm"
    (writer.to_parquet if a.format == "parquet" else writer.to_csv)(star.all_tables(), star_dir)
    CdmMapper().write_cdm_folder(
        tables=tables,
        output_dir=cdm_dir,
        domain_name="ShapeRetail",
        entity_map=domain.cdm_map(),
        fmt=a.format,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
