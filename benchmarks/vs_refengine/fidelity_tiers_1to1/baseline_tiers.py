"""The baseline's fidelity tiers 1-3 on one pair of datasets, as JSON (run with ``$REFENGINE_PY``).

    "$REFENGINE_PY" baseline_tiers.py REAL_DIR SYNTH_DIR --out FILE [--tables a,b]
        [--no-tier1] [--small-rows N] [--seed S]

Both directories hold one ``<table>.parquet`` per table, loaded with ``pandas.read_parquet`` as
``domain_1to1/verify.py`` loads its runs. Per table the output holds, unrounded:

* ``tier1``: ``AdvancedProfiler.profile_pair(real, synth)`` (and ``profile_single(real)`` for tables
  of at most ``--small-rows`` rows), with seconds taken;
* ``tier2``: ``run_tier2(real, synth)``;
* ``tier3``: ``ChowLiuNetwork().fit`` of each side and ``DriftMonitor().compare(real, synth)``;
* for tables of at most ``--small-rows`` rows: ``bootstrap`` (``BootstrapMode().generate``, seed
  ``--seed``, 1000 rows) and ``dp`` (``DifferentialPrivacy.apply`` with ``default_rng(--seed)``,
  Laplace and Gaussian).

The checkout is only read.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import sys
import time
import warnings
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parents[1]))
import _refpkg  # noqa: E402


def jsonable(x: Any) -> Any:
    import numpy as np
    import pandas as pd

    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        return jsonable(dataclasses.asdict(x))
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    if isinstance(x, (np.bool_, bool)):
        return bool(x)
    if isinstance(x, (np.integer, int)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        return None if not math.isfinite(float(x)) else float(x)
    if x is pd.NaT or x is None:
        return None
    if isinstance(x, pd.Timestamp):
        return int(x.value)
    return str(x)


def frame_json(df: Any) -> dict[str, list[Any]]:
    import numpy as np
    import pandas as pd

    out: dict[str, list[Any]] = {}
    for c in df.columns:
        s = df[c]
        if pd.api.types.is_datetime64_any_dtype(s):
            out[c] = [None if pd.isna(v) else int(v.value) for v in s]
        else:
            out[c] = [jsonable(None if (not isinstance(v, str) and pd.isna(v)) else v) for v in s]
        _ = np
    return out


def load(path: Path, tables: list[str] | None) -> dict[str, Any]:
    import pandas as pd

    names = tables or sorted(p.stem for p in path.glob("*.parquet"))
    return {t: pd.read_parquet(path / f"{t}.parquet") for t in names}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("real")
    ap.add_argument("synth")
    ap.add_argument("--out", required=True)
    ap.add_argument("--tables")
    ap.add_argument("--no-tier1", action="store_true")
    ap.add_argument("--psi-only", action="store_true", help="only the PSI of each numeric column")
    ap.add_argument(
        "--adversarial-only",
        action="store_true",
        help="only the adversarial test (its result depends on PYTHONHASHSEED)",
    )
    ap.add_argument("--small-rows", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(argv)
    warnings.simplefilter("ignore")
    import numpy as np

    only = a.tables.split(",") if a.tables else None
    real, synth = load(Path(a.real), only), load(Path(a.synth), only)
    import sklearn

    out: dict[str, Any] = {"sklearn": sklearn.__version__, "numpy": np.__version__, "tables": {}}
    if a.psi_only:
        out["tables"] = psi_tables(real, synth)
    elif a.adversarial_only:
        out["tables"] = adversarial_tables(real, synth)
    else:
        for name, r in real.items():
            out["tables"][name] = tiers_for(
                name, r, synth[name], tier1=not a.no_tier1, small_rows=a.small_rows, seed=a.seed
            )
            print(f"baseline {name}: done", file=sys.stderr, flush=True)
    Path(a.out).write_text(json.dumps(out), encoding="utf-8")
    return 0


def psi_tables(real: dict[str, Any], synth: dict[str, Any]) -> dict[str, Any]:
    """The baseline's PSI of each numeric column, on the 5000-row samples its drift test takes."""
    import pandas as pd

    psi = _refpkg.mod("inference.tier3_research")._population_stability_index

    out: dict[str, Any] = {}
    for name, r in real.items():
        cols: dict[str, float] = {}
        for c in r.columns:
            if c not in synth[name].columns or c.startswith(_refpkg.FIELD_PREFIX):
                continue
            x, y = r[c].dropna(), synth[name][c].dropna()
            if len(x) < 10 or len(y) < 10 or not pd.api.types.is_numeric_dtype(x):
                continue
            x = x.sample(5000, random_state=0) if len(x) > 5000 else x
            y = y.sample(5000, random_state=0) if len(y) > 5000 else y
            cols[c] = jsonable(psi(x, y))
        out[name] = {"psi": cols}
    return out


def adversarial_tables(real: dict[str, Any], synth: dict[str, Any]) -> dict[str, Any]:
    """The baseline's adversarial test alone. It takes the classifier's features in the iteration
    order of a ``set`` of column names, so its numbers depend on ``PYTHONHASHSEED``; ``run.py``
    runs this under several seeds to measure the baseline's own spread."""
    AdvancedProfiler = _refpkg.mod("inference.advanced_profiler").AdvancedProfiler

    out: dict[str, Any] = {}
    for name, r in real.items():
        adv = AdvancedProfiler()._adversarial_test(r, synth[name])
        out[name] = {"adversarial": jsonable(adv)}
    return out


def tiers_for(
    name: str, r: Any, s: Any, *, tier1: bool, small_rows: int, seed: int
) -> dict[str, Any]:
    """Every tier output of the baseline for one pair of frames (see the module docstring)."""
    import numpy as np

    AdvancedProfiler = _refpkg.mod("inference.advanced_profiler").AdvancedProfiler
    run_tier2 = _refpkg.mod("inference.tier2_profiler").run_tier2
    BootstrapMode = _refpkg.mod("inference.tier3_research").BootstrapMode
    ChowLiuNetwork = _refpkg.mod("inference.tier3_research").ChowLiuNetwork
    DifferentialPrivacy = _refpkg.mod("inference.tier3_research").DifferentialPrivacy
    DriftMonitor = _refpkg.mod("inference.tier3_research").DriftMonitor

    t: dict[str, Any] = {"rows": len(r)}
    if tier1:
        t0 = time.perf_counter()
        t["tier1"] = jsonable(AdvancedProfiler().profile_pair(r, s, name))
        t["tier1_s"] = time.perf_counter() - t0
        if len(r) <= small_rows:
            t["tier1_single"] = jsonable(AdvancedProfiler().profile_single(r, name))
    t["tier2"] = jsonable(run_tier2(r, s))
    t["tier3"] = {
        "chow_liu_real": jsonable(ChowLiuNetwork().fit(r)),
        "chow_liu_synth": jsonable(ChowLiuNetwork().fit(s)),
        "drift": jsonable(DriftMonitor().compare(r, s)),
    }
    if len(r) <= small_rows:
        boot, res = BootstrapMode().generate(r, n_rows=1000, table_name=name, seed=seed)
        t["bootstrap"] = {"frame": frame_json(boot), "result": jsonable(res)}
        t["dp"] = {}
        for mech in ("laplace", "gaussian"):
            noised, dpr = DifferentialPrivacy(epsilon=0.5, mechanism=mech).apply(
                r, rng=np.random.default_rng(seed)
            )
            t["dp"][mech] = {"frame": frame_json(noised), "result": jsonable(dpr)}
    return t


if __name__ == "__main__":
    raise SystemExit(main())
