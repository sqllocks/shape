"""Write the retail domain's data into the ``shape-domains`` plugin (D-10, P4-07).

    source scripts/env.sh
    "$SHAPE_VENV/bin/python" benchmarks/vs_spindle/domain_1to1/export_retail.py          # write
    "$SHAPE_VENV/bin/python" benchmarks/vs_spindle/domain_1to1/export_retail.py --check  # equal

* ``schema.json``: the baseline's retail schema (``fixtures/schemas/retail_3nf.json``, from
  ``dump_schema.py``) as a Shape generation schema.
* ``reference/<name>.arrow``: the four reference datasets the schema reads
  (``categories``, ``product_names``, ``promo_names``, ``us_zip_locations``), copied from the
  baseline checkout, which is only read. Row order and values are the baseline's.

``--check`` regenerates both in memory and compares them with the shipped files. Runs in the
Shape venv; ``$SPINDLE_ROOT`` is needed for the reference data.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from paths import SHAPE_ROOT, SPINDLE_ROOT  # noqa: E402
from schema_import import import_dump  # noqa: E402

DATA = SHAPE_ROOT / "plugins" / "shape-domains" / "src" / "shape_domains" / "data" / "retail"
SCHEMA_FIXTURE = HERE.parent / "fixtures" / "schemas" / "retail_3nf.json"
SOURCE = SPINDLE_ROOT / "sqllocks_spindle" / "domains" / "retail" / "reference_data"
DATASETS = ("categories", "product_names", "promo_names", "us_zip_locations")


def schema_document() -> dict[str, Any]:
    """The schema as Shape reads it. One addition to the baseline's: its uniform ``temporal``
    columns are nanosecond timestamps (pandas ``datetime64[ns]``), so they carry ``unit: ns``
    to give the same Arrow type (T-21 (a)); ``derived`` dates follow their source's unit."""
    doc: dict[str, Any] = import_dump(json.loads(SCHEMA_FIXTURE.read_text("utf-8"))).to_dict()
    for table in doc["tables"].values():
        for column in table["columns"].values():
            gen = column["generator"]
            if gen.get("strategy") == "temporal" and gen.get("pattern") == "uniform":
                gen["unit"] = "ns"
    return doc


def dataset_table(name: str) -> pa.Table:
    rows = json.loads((SOURCE / f"{name}.json").read_text("utf-8"))
    if rows and isinstance(rows[0], dict):
        fields = list(rows[0])
        return pa.table({f: pa.array([r.get(f) for r in rows]) for f in fields})
    return pa.table({"value": pa.array(rows, type=pa.string())})


def render_schema(doc: dict[str, Any]) -> str:
    return json.dumps(doc, indent=1, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="compare, write nothing")
    check = ap.parse_args(argv).check
    problems: list[str] = []

    schema_file = DATA / "schema.json"
    text = render_schema(schema_document())
    if check:
        if not schema_file.exists() or schema_file.read_text("utf-8") != text:
            problems.append(str(schema_file))
    else:
        DATA.mkdir(parents=True, exist_ok=True)
        schema_file.write_text(text, "utf-8")

    for name in DATASETS:
        table = dataset_table(name)
        target = DATA / "reference" / f"{name}.arrow"
        if check:
            if not target.exists() or not pa.ipc.open_file(target).read_all().equals(table):
                problems.append(str(target))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Arrow IPC (uncompressed): reading it is a copy, where Parquet is a decode that costs
            # tens of milliseconds the first time it runs in a process.
            with pa.ipc.new_file(str(target), table.schema) as writer:
                writer.write_table(table)
    if problems:
        print("differs from the baseline: " + ", ".join(problems), file=sys.stderr)
        return 1
    print("retail data " + ("matches" if check else "written"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
