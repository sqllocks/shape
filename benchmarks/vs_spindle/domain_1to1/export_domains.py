"""Write the data of the ``shape-domains`` plugin's non-retail domains (D-10, P6-01).

    source scripts/env.sh
    "$SHAPE_VENV/bin/python" benchmarks/vs_spindle/domain_1to1/export_domains.py          # write
    "$SHAPE_VENV/bin/python" benchmarks/vs_spindle/domain_1to1/export_domains.py --check  # equal
    ... export_domains.py --domain education                                              # one

Per domain: ``schema.json`` and ``schema_star.json`` (the baseline's dumps in
``fixtures/schemas/`` as Shape generation schemas, with the same ``unit: ns`` addition as
``export_retail.py``) and ``reference/<name>.arrow`` for every reference file the baseline's domain
has (Arrow IPC; row order and values are the baseline's). ``--check`` regenerates everything in
memory and compares it with the shipped files. Runs in the Shape venv; ``$SPINDLE_ROOT`` is read.

One deliberate difference from the baseline's dump, ``OVERRIDES``: a column whose reference
lookup cannot resolve is the empty string in the baseline (``capital_markets.industry.industry_name``
names the nested field ``industries.industry_name``, which the baseline does not read, so it falls
back to the missing ``name`` key and yields ``""`` for every row). The schema says so with
``constant ""``; ``--check`` proves nothing else differs.
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
sys.path.insert(0, str(HERE))
import export_retail  # noqa: E402
from paths import SHAPE_ROOT, SPINDLE_ROOT  # noqa: E402

DATA = SHAPE_ROOT / "plugins" / "shape-domains" / "src" / "shape_domains" / "data"
FIXTURES = HERE.parent / "fixtures" / "schemas"
SOURCE = SPINDLE_ROOT / "sqllocks_spindle" / "domains"
# Reference datasets each domain ships (financial also reads retail's ``us_zip_locations``).
DOMAINS: dict[str, tuple[str, ...]] = {
    "capital_markets": ("exchanges", "gics_sectors", "index_memberships", "sp500_constituents"),
    "education": ("aid_types", "course_catalog", "department_names"),
    "financial": ("branch_names", "merchant_names", "transaction_categories"),
}
OVERRIDES: dict[tuple[str, str, str], dict[str, Any]] = {
    ("capital_markets", "industry", "industry_name"): {"strategy": "constant", "value": ""},
}


def schema_document(domain: str, mode: str) -> dict[str, Any]:
    doc = export_retail.schema_document(FIXTURES / f"{domain}_{mode}.json")
    for (d, table, column), generator in OVERRIDES.items():
        if d == domain:
            doc["tables"][table]["columns"][column]["generator"] = dict(generator)
    return doc


def dataset_table(domain: str, name: str) -> pa.Table:
    rows = json.loads((SOURCE / domain / "reference_data" / f"{name}.json").read_text("utf-8"))
    if rows and isinstance(rows[0], dict):
        fields = list(rows[0])
        return pa.table({f: pa.array([r.get(f) for r in rows]) for f in fields})
    return pa.table({"value": pa.array(rows, type=pa.string())})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="compare, write nothing")
    ap.add_argument("--domain", choices=sorted(DOMAINS), help="one domain (default: all)")
    args = ap.parse_args(argv)
    problems: list[str] = []
    for domain in [args.domain] if args.domain else sorted(DOMAINS):
        folder = DATA / domain
        for filename, mode in (("schema.json", "3nf"), ("schema_star.json", "star")):
            target = folder / filename
            text = export_retail.render_schema(schema_document(domain, mode))
            if args.check:
                if not target.exists() or target.read_text("utf-8") != text:
                    problems.append(str(target))
            else:
                folder.mkdir(parents=True, exist_ok=True)
                target.write_text(text, "utf-8")
        for name in DOMAINS[domain]:
            table = dataset_table(domain, name)
            target = folder / "reference" / f"{name}.arrow"
            if args.check:
                if not target.exists() or not pa.ipc.open_file(target).read_all().equals(table):
                    problems.append(str(target))
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with pa.ipc.new_file(str(target), table.schema) as writer:
                    writer.write_table(table)
    if problems:
        print("differs from the baseline: " + ", ".join(problems), file=sys.stderr)
        return 1
    print("domain data " + ("matches" if args.check else "written"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
