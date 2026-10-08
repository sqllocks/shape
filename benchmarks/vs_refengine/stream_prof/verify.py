"""Equivalence check for STREAM-PROF (gate G3): a bounded-mode stream replay of D2 equals batch
profiling of D2 in bounded mode, within the T-14 bounds, and is identical across processes.

    source scripts/env.sh
    "$SHAPE_VENV/bin/python" benchmarks/vs_refengine/stream_prof/verify.py \
        [--rows N] [--batch 65536] [--file d2.parquet]

The replay is the real path: D2 is read from Parquet, cut into micro-batches, and fed to a
``GlobalProfiler`` (the bounded-mode stream runtime); the batch side is ``profile_table`` with
``mode="bounded"`` on the same file. Both use the same T-14 sketches, so everything that is a
count, a flag, a top-value list or an error model must be equal; floating-point moments and
quantiles must agree to 1e-9 relative (they are in fact equal unless the batch cut changes the
order of additions). Then the replay runs in two further fresh processes with different
``PYTHONHASHSEED`` values and the three documents must be byte-identical.

Exit 0 when everything holds, 1 otherwise. Timing is `bench.py`, and counts only after this
exits 0.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from paths import PROFILE_DATA_DIR  # noqa: E402

REL = 1e-9


def replay(path: Path, batch_rows: int, rows: int | None) -> dict:
    """The stream-runtime profile of ``path``, replayed in micro-batches (a document)."""
    import pyarrow.parquet as pq

    from shape.profile.engine import _document, configure_threads
    from shape.streaming.runtime import GlobalProfiler

    configure_threads(None)
    pf = pq.ParquetFile(path)
    prof = GlobalProfiler(pf.schema_arrow, name="d2")
    seen = 0
    for batch in pf.iter_batches(batch_size=batch_rows):
        if rows is not None and seen + batch.num_rows > rows:
            batch = batch.slice(0, rows - seen)
        if batch.num_rows:
            prof.process(batch)
            seen += batch.num_rows
        if rows is not None and seen >= rows:
            break
    (window,) = prof.finish()
    return _document("bounded", {"d2": window.profile})


def batch_profile(path: Path, rows: int | None) -> dict:
    from shape.profile.engine import EngineOptions, _document, profile_table

    source: object = path
    if rows is not None:
        import pyarrow.parquet as pq

        source = pq.read_table(path).slice(0, rows)
    entry = profile_table(source, "d2", EngineOptions(mode="bounded"))
    return _document("bounded", {"d2": entry})


def digest(doc: dict) -> str:
    return hashlib.sha256(json.dumps(doc, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _close(a: object, b: object, where: str, problems: list[str]) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        if a.keys() != b.keys():
            problems.append(f"{where}: keys differ: {sorted(set(a) ^ set(b))}")
            return
        for k in a:
            _close(a[k], b[k], f"{where}.{k}", problems)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            problems.append(f"{where}: length {len(a)} != {len(b)}")
            return
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            _close(x, y, f"{where}[{i}]", problems)
    elif isinstance(a, float) or isinstance(b, float):
        if a is None or b is None or not math.isclose(a, b, rel_tol=REL, abs_tol=1e-300):
            problems.append(f"{where}: {a!r} != {b!r}")
    elif a != b:
        problems.append(f"{where}: {a!r} != {b!r}")


def compare(stream: dict, batch: dict) -> list[str]:
    """Differences between the two documents (empty when equivalent)."""
    problems: list[str] = []
    s, b = stream["tables"]["d2"], batch["tables"]["d2"]
    if s["rows"] != b["rows"]:
        problems.append(f"rows {s['rows']} != {b['rows']}")
    sc = {c["name"]: c for c in s["columns"]}
    bc = {c["name"]: c for c in b["columns"]}
    if list(sc) != list(bc):
        problems.append("column names or order differ")
    for name in bc:
        if name in sc:
            _close(sc[name], bc[name], name, problems)
    if stream["mode"] != "bounded" or batch["mode"] != "bounded":
        problems.append("both sides must be bounded mode")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--file", default="d2.parquet", help="D2 under the profiling data directory")
    ap.add_argument("--rows", type=int, default=None, help="use the first N rows only")
    ap.add_argument("--batch", type=int, default=65_536, help="micro-batch rows (default 64k)")
    ap.add_argument("--emit", action="store_true", help="(internal) print the replay digest")
    args = ap.parse_args(argv)
    path = PROFILE_DATA_DIR / args.file
    if not path.exists():
        print(f"missing {path}: run profile_1to1/datasets.py D2", file=sys.stderr)
        return 1
    if args.emit:
        print(digest(replay(path, args.batch, args.rows)))
        return 0

    print(f"batch bounded profile of {path.name} ...", flush=True)
    batch = batch_profile(path, args.rows)
    print(f"stream replay in {args.batch}-row micro-batches ...", flush=True)
    stream = replay(path, args.batch, args.rows)
    problems = compare(stream, batch)
    for p in problems[:20]:
        print("DIFF", p)
    ok = not problems
    print(f"stream == batch (bounded, rel {REL:g}): {'PASS' if ok else 'FAIL'}", flush=True)

    digests = {digest(stream)}
    for seed in ("0", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        cmd = [sys.executable, __file__, "--emit", "--file", args.file, "--batch", str(args.batch)]
        if args.rows is not None:
            cmd += ["--rows", str(args.rows)]
        out = subprocess.run(cmd, env=env, capture_output=True, text=True, check=True).stdout
        digests.add(out.strip())
    same = len(digests) == 1
    print(
        f"identical across processes (3 runs, PYTHONHASHSEED varies): {'PASS' if same else 'FAIL'}"
    )
    return 0 if ok and same else 1


if __name__ == "__main__":
    raise SystemExit(main())
