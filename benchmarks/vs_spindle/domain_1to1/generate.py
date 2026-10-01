"""Generate one run of a domain and write it as Parquet.

    source scripts/env.sh
    G=benchmarks/vs_spindle/domain_1to1/generate.py
    "$SPINDLE_PY" $G --impl spindle --domain retail --scale small --seed 42
    "$SHAPE_VENV/bin/python" $G --impl reference_port --domain retail --scale small --seed 1042
    "$SHAPE_VENV/bin/python" $G --impl shape --domain retail --scale small --seed 1042

Each implementation runs in its own venv (Spindle's or Shape's). Output layout:

    $BENCH_OUT_DIR/<impl>/<domain>/<scale>/seed<N>/<table>.parquet   (+ _SUCCESS, written last)

* ``spindle``: ``Spindle().generate`` + ``PandasWriter.to_parquet`` (the
  ``spindle generate --format parquet`` path: pandas ``to_parquet``, snappy).
* ``reference_port``: the numpy + pyarrow port in ``port.py``. Retail only: exits 2 for any
  other domain.
* ``shape``: the product path: the ``shape.domains`` plugin, ``Engine.generate`` and the
  product's Parquet writer. Exits 2 for a domain no installed plugin provides.

Exit codes: 0 ok, 2 the implementation cannot generate this domain.
The timed region (setup + generate + write, imports excluded) is reported on the last stdout
line as ``GEN_JSON {...}``; bench.py uses the same code path.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from paths import BENCH_OUT_DIR, SPINDLE_ROOT  # noqa: E402

IMPLS = ("spindle", "reference_port", "shape")
SUCCESS = "_SUCCESS"


class Unsupported(Exception):
    """The implementation cannot generate the requested domain (exit code 2)."""


def out_dir(impl: str, domain: str, scale: str, seed: int) -> Path:
    return BENCH_OUT_DIR / impl / domain / scale / f"seed{seed}"


def is_complete(path: Path, tables: list[str] | None = None) -> bool:
    """A run directory is complete when generation finished (``_SUCCESS``) and, if the table
    names are given, every table's Parquet file is there."""
    if not (path / SUCCESS).exists():
        return False
    return all((path / f"{t}.parquet").exists() for t in (tables or []))


def _peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _ru() -> dict[str, float]:
    r = resource.getrusage(resource.RUSAGE_SELF)
    return {"user": r.ru_utime, "sys": r.ru_stime, "minflt": r.ru_minflt, "majflt": r.ru_majflt}


def _run_spindle(domain: str, scale: str, seed: int, dest: Path) -> dict:
    t_imp = time.perf_counter()
    sys.path.insert(0, str(SPINDLE_ROOT))
    import numpy  # noqa: F401
    import pandas  # noqa: F401
    import pyarrow  # noqa: F401
    from sqllocks_spindle import Spindle
    from sqllocks_spindle.cli import _resolve_domain
    from sqllocks_spindle.output.pandas_writer import PandasWriter

    import_s = time.perf_counter() - t_imp
    marks: list[tuple[str, float]] = []
    ru0, t0 = _ru(), time.perf_counter()
    dom = _resolve_domain(domain, "3nf")
    sp = Spindle()
    t_setup = time.perf_counter()
    res = sp.generate(
        domain=dom,
        scale=scale,
        seed=seed,
        on_progress=lambda name, done, total: marks.append((name, time.perf_counter())),
    )
    t1 = time.perf_counter()
    PandasWriter().to_parquet(res.tables, dest)
    t2 = time.perf_counter()
    per_table, prev = {}, t_setup
    for name, t in marks:
        per_table[name] = t - prev
        prev = t
    per_table["_compute_phase+business_rules"] = t1 - prev
    per_table["_construct_domain+Spindle()"] = t_setup - t0
    return {
        "import_s": import_s,
        "gen_s": t1 - t0,
        "write_s": t2 - t1,
        "total_s": t2 - t0,
        "rows": {k: len(v) for k, v in res.tables.items()},
        "per_table_s": per_table,
        "ru0": ru0,
    }


def _run_reference_port(domain: str, scale: str, seed: int, dest: Path) -> dict:
    if domain != "retail":
        raise Unsupported(f"reference_port supports retail only, not {domain!r}")
    t_imp = time.perf_counter()
    import numpy
    import pyarrow

    sys.path.insert(0, str(HERE))
    import port

    # pyarrow lazily imports pandas (if installed) on the first pa.array(list); Spindle
    # imports pandas before its timed region, so do the same here.
    pyarrow.array(["warm"])
    import_s = time.perf_counter() - t_imp
    ru0, t0 = _ru(), time.perf_counter()
    tables, eng = port.generate(scale, seed, SPINDLE_ROOT, return_engine=True)
    t1 = time.perf_counter()
    port.write_parquet(tables, dest)
    t2 = time.perf_counter()
    return {
        "import_s": import_s,
        "gen_s": t1 - t0,
        "write_s": t2 - t1,
        "total_s": t2 - t0,
        "rows": {k: v.num_rows for k, v in tables.items()},
        "per_table_s": dict(eng.timings),
        "per_column_s": dict(eng.col_timings),
        "numpy": numpy.__version__,
        "pyarrow": pyarrow.__version__,
        "ru0": ru0,
    }


def _run_shape(domain: str, scale: str, seed: int, dest: Path) -> dict:
    t_imp = time.perf_counter()
    import numpy
    import pyarrow

    from shape.generation.domains import DomainNotFoundError, domain_names, load_domain
    from shape.generation.engine import Engine
    from shape.generation.output import write_engine
    from shape.plugins.host import default_host

    # Everything the timed region touches is imported before it (T-19: imports are excluded for
    # both tools; the baseline's import loads every strategy and writer): the strategy and sink
    # plugins are loaded and the domain entry points discovered. pyarrow lazily imports pandas on
    # its first list array.
    pyarrow.array(["warm"])
    host = default_host()
    host.load_all("shape.strategies")
    host.load_all("shape.sinks")
    known = domain_names()
    import_s = time.perf_counter() - t_imp
    if domain not in known:
        raise Unsupported(f"impl 'shape' has no domain {domain!r} (installed: {known})")
    ru0, t0 = _ru(), time.perf_counter()
    try:
        loaded = load_domain(domain)
    except DomainNotFoundError as e:
        raise Unsupported(str(e)) from e
    engine = Engine(loaded.schema, scale=scale, seed=seed)
    t_setup = time.perf_counter()
    # The product path: tables are written (parallel, snappy Parquet, T-17) as soon as they are
    # final, while the others are still being generated, so generate and write overlap.
    write_engine(engine, "parquet", dest)
    t1 = time.perf_counter()
    return {
        "import_s": import_s,
        "gen_s": t1 - t0,
        "write_s": 0.0,
        "total_s": t1 - t0,
        "overlapped": True,
        "rows": {name: engine.row_counts[name] for level in engine.levels for name in level},
        "per_table_s": {"_construct_domain+Engine": t_setup - t0, "_generate+write": t1 - t_setup},
        "numpy": numpy.__version__,
        "pyarrow": pyarrow.__version__,
        "ru0": ru0,
    }


RUNNERS = {"spindle": _run_spindle, "reference_port": _run_reference_port, "shape": _run_shape}


def run(impl: str, domain: str, scale: str, seed: int) -> dict:
    """Generate and write one run into its layout directory (replacing any previous one).
    Returns the timing record. Raises ``Unsupported`` for a domain the impl cannot do."""
    final = out_dir(impl, domain, scale, seed)
    tmp = final.with_name(final.name + f".tmp{os.getpid()}")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    try:
        rec = RUNNERS[impl](domain, scale, seed, tmp)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    ru0 = rec.pop("ru0")
    ru1 = _ru()
    rec.update(
        {
            "user_s": ru1["user"] - ru0["user"],
            "sys_s": ru1["sys"] - ru0["sys"],
            "minor_faults": ru1["minflt"] - ru0["minflt"],
            "major_faults": ru1["majflt"] - ru0["majflt"],
            "cpu_s": (ru1["user"] + ru1["sys"]) - (ru0["user"] + ru0["sys"]),
            "peak_rss_mb": _peak_rss_mb(),
            "parquet_bytes": sum(p.stat().st_size for p in tmp.glob("*.parquet")),
        }
    )
    (tmp / SUCCESS).write_text(
        json.dumps(
            {"impl": impl, "domain": domain, "scale": scale, "seed": seed, "rows": rec["rows"]}
        )
    )
    if final.exists():
        shutil.rmtree(final)
    tmp.rename(final)
    return rec


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--impl", choices=IMPLS, required=True)
    ap.add_argument("--domain", required=True)
    ap.add_argument("--scale", default="small")
    ap.add_argument("--seed", type=int, required=True)
    a = ap.parse_args(argv)
    try:
        rec = run(a.impl, a.domain, a.scale, a.seed)
    except Unsupported as e:
        print(f"unsupported: {e}", file=sys.stderr)
        return 2
    print(
        f"wrote {out_dir(a.impl, a.domain, a.scale, a.seed)} "
        f"({sum(rec['rows'].values()):,} rows, {rec['total_s']:.2f}s)"
    )
    print("GEN_JSON " + json.dumps(rec))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
