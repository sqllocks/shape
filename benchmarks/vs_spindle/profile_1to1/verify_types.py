"""Check ``shape.profile.infer.infer_spindle_type`` against Spindle's own dtype (T-22 dtype rule).

    source scripts/env.sh && "$SHAPE_VENV/bin/python" \
        benchmarks/vs_spindle/profile_1to1/verify_types.py [dataset ...]

For every column of every T-22 dataset (D1-D4, MT, EDGE; CSV and Parquet) the dtype from
``infer_spindle_type`` is compared with the dtype in Spindle's cached ``DataProfiler`` output
(``$BENCH_OUT_DIR/profile_cache/spindle_json``, filled by ``verify.py``). Exits 1 on any
difference, 2 if a dataset or Spindle output is missing. Run ``verify.py --impl reference_port``
first to fill the cache.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import verify  # noqa: E402  (dataset list and Spindle cache)
from paths import BENCH_OUT_DIR  # noqa: E402

from shape.io import PANDAS_CSV, read_table  # noqa: E402
from shape.profile.infer import infer_spindle_type  # noqa: E402


def _spindle_dtypes(ds: str) -> dict[str, dict[str, str]]:
    """{table: {column: dtype}} from the cached Spindle output."""
    out = json.loads((verify.CACHE / f"{ds.replace('/', '_')}.json").read_text())
    tables = out["tables"] if "tables" in out else {out["name"]: out}
    return {t: {c: v["dtype"] for c, v in d["columns"].items()} for t, d in tables.items()}


def _tables(ds: str):
    """(table name, Arrow table, source kind) for a dataset name used by verify.py."""
    data = verify.DATA
    if ds in ("mt", "mt.parquet"):
        ext = ".parquet" if ds == "mt.parquet" else ".csv"
        for p in sorted((data / "mt").glob("*" + ext)):
            yield p.stem, p, ext
        return
    p = data / ds
    yield p.stem, p, p.suffix


def main(argv: list[str] | None = None) -> int:
    wanted = [a for a in (argv if argv is not None else sys.argv[1:])] or verify.ALL
    checked = bad = 0
    for ds in wanted:
        cache = verify.CACHE / f"{ds.replace('/', '_')}.json"
        if not cache.exists():
            print(
                f"missing Spindle output for {ds} in {BENCH_OUT_DIR}; run verify.py first",
                file=sys.stderr,
            )
            return 2
        spindle = _spindle_dtypes(ds)
        for name, path, ext in _tables(ds):
            if not path.exists():
                print(f"missing dataset {path}; run datasets.py", file=sys.stderr)
                return 2
            csv = ext == ".csv"
            table = read_table(path, csv=PANDAS_CSV) if csv else read_table(path)
            want = spindle[name if name in spindle else next(iter(spindle))]
            for col in table.column_names:
                got = infer_spindle_type(table[col], source="csv" if csv else "arrow")
                checked += 1
                if got != want[col]:
                    bad += 1
                    print(f"DIFF {ds}:{name}.{col}: infer={got} spindle={want[col]}")
        print(f"{ds}: ok", flush=True)
    print(f"{checked} columns checked, {bad} differ")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
