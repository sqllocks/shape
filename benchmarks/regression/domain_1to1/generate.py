"""Generate the same Shape path in either interpreter; seeds fix the output role."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from common import peak_rss_mb, rusage  # noqa: E402 (harness path bootstrap)
from paths import BASELINE_VERSION, BENCH_OUT_DIR  # noqa: E402 (harness path bootstrap)

IMPLS = ("shape",)
SUCCESS = "_SUCCESS"


def validate_seed(tool: str, seed: int) -> None:
    allowed = (42, 43, 44, 45, 46) if tool == "baseline" else (1042,)
    if seed not in allowed:
        raise ValueError(f"{tool}: seed {seed} is not in fixed set {allowed}")


def composite_spec(domain: str) -> str:
    return domain.removeprefix("composite_").replace("-", "+")


def is_composite(domain: str) -> bool:
    return domain.startswith("composite_") or "+" in domain


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
    return peak_rss_mb()


def _ru() -> dict[str, float]:
    r = rusage()
    return {"user": r.utime, "sys": r.stime, "minflt": r.minflt, "majflt": r.majflt}


def _run_shape(domain: str, scale: str, seed: int, dest: Path) -> dict[str, Any]:
    t_imp = time.perf_counter()
    import numpy
    import pyarrow  # type: ignore[import-untyped]

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
    from shape.kernel.dispatch import get_kernel

    get_kernel()  # the native extension is an import too (12 ms), not part of the run
    import_s = time.perf_counter() - t_imp
    if domain not in known and not is_composite(domain):
        raise Unsupported(f"impl 'shape' has no domain {domain!r} (installed: {known})")
    ru0, t0 = _ru(), time.perf_counter()
    try:
        if is_composite(domain):
            from shape.generation.composite import resolve

            schema = resolve(composite_spec(domain)).schema
        else:
            schema = load_domain(domain).schema
    except DomainNotFoundError as e:
        raise Unsupported(str(e)) from e
    engine = Engine(schema, scale=scale, seed=seed)
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


def run(impl: str, domain: str, scale: str, seed: int) -> dict[str, Any]:
    """Generate and write one run into its layout directory (replacing any previous one).
    Returns the timing record. Raises ``Unsupported`` for a domain the impl cannot do."""
    tool = "baseline" if seed in (42, 43, 44, 45, 46) else "shape"
    validate_seed(tool, seed)
    if tool == "baseline":
        import shape

        if shape.__version__ != BASELINE_VERSION:
            raise Unsupported("baseline version differs from declared release")
    final = out_dir(tool, domain, scale, seed)
    tmp = final.with_name(final.name + f".tmp{os.getpid()}")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    try:
        rec = _run_shape(domain, scale, seed, tmp)
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
    except (Unsupported, ValueError) as e:
        print(f"unsupported: {e}", file=sys.stderr)
        return 2
    tool = "baseline" if a.seed in (42, 43, 44, 45, 46) else "shape"
    print(
        f"wrote {out_dir(tool, a.domain, a.scale, a.seed)} "
        f"({sum(rec['rows'].values()):,} rows, {rec['total_s']:.2f}s)"
    )
    print("GEN_JSON " + json.dumps(rec))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
