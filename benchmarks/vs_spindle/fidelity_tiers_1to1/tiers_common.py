"""Shape's tier outputs in the baseline's JSON shape, and the field-by-field comparison.

Imported by ``run.py`` and by ``tests/benchmarks/test_fidelity_tiers_1to1.py`` (Shape's venv: numpy,
pyarrow, and scikit-learn for the parts that need it).

Comparison rules (plan P4-11, T-22):

* integers, booleans, strings, ``None`` and list lengths: equal;
* floats: equal within 1e-9 relative (and 1e-12 absolute), T-22's bound for means and spreads;
* **loose** floats, within ``LOOSE`` (0.02) of the baseline's: the adversarial AUC and accuracy and
  every field of a mixture fit (weights absolute; means, spreads, BIC and AIC relative to the
  larger of the two and 1), and the importances of the adversarial test's features (the
  classifier is fitted on the baseline's feature order, which is hash-seed dependent: the
  importances may also lie inside the range the baseline covers across hash seeds, ``spread``);
* ``top_features`` names are compared as sets when the importances differ (ties at zero);
* keys Shape adds (``notes``, ``psi``, ``passing_rate``, ``distinguishability_score``) are ignored.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import pyarrow as pa

LOOSE = 0.02
REL = 1e-9
ABS = 1e-12
IGNORED_KEYS = {"notes", "psi", "passing_rate", "distinguishability_score", "tier1_s"}


def frame_json(table: pa.Table) -> dict[str, list[Any]]:
    """A table as column -> list, the form the baseline script writes (timestamps as ns)."""
    out: dict[str, list[Any]] = {}
    for name in table.column_names:
        col = table.column(name)
        if pa.types.is_timestamp(col.type):
            ns = col.cast(pa.timestamp("ns")).cast(pa.int64())
            out[name] = ns.to_pylist()
        else:
            out[name] = [_json_value(v) for v in col.to_pylist()]
    return out


def _json_value(v: Any) -> Any:
    """What the baseline script's ``jsonable`` makes of a value: numbers, booleans and strings as
    they are, missing and non-finite as None, anything else (dates, decimals) as its ``str``."""
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    return str(v)


def shape_tiers(
    name: str, real: pa.Table, synth: pa.Table, *, tier1: bool, small_rows: int, seed: int
) -> dict[str, Any]:
    """The same fields as ``baseline_tiers.tiers_for``, from Shape."""
    from shape.fidelity.tier1 import Tier1Profiler
    from shape.fidelity.tier2 import run_tier2
    from shape.fidelity.tier3 import ChowLiuTree, DriftMonitor, bootstrap_table
    from shape.privacy.dp import DifferentialPrivacy

    t: dict[str, Any] = {"rows": real.num_rows}
    if tier1:
        t["tier1"] = Tier1Profiler().profile_pair(real, synth, name).to_dict()
        if real.num_rows <= small_rows:
            t["tier1_single"] = Tier1Profiler().profile_single(real, name).to_dict()
    t["tier2"] = run_tier2(real, synth).to_dict()
    tree = ChowLiuTree()
    t["tier3"] = {
        "chow_liu_real": tree.fit(real).to_dict(),
        "chow_liu_synth": tree.fit(synth).to_dict(),
        "drift": DriftMonitor().compare(real, synth).to_dict(),
    }
    if real.num_rows <= small_rows:
        boot, res = bootstrap_table(real, n_rows=1000, table_name=name, seed=seed)
        t["bootstrap"] = {"frame": frame_json(boot), "result": _plain(res)}
        t["dp"] = {}
        for mech in ("laplace", "gaussian"):
            noised, dpr = DifferentialPrivacy(epsilon=0.5, mechanism=mech).apply(real, seed=seed)
            t["dp"][mech] = {"frame": frame_json(noised), "result": dpr.to_dict()}
    return t


def _plain(x: Any) -> Any:
    import dataclasses

    return dataclasses.asdict(x)


@dataclass
class Diff:
    mismatches: list[str] = field(default_factory=list)
    compared: int = 0
    max_loose: float = 0.0  # largest |difference| seen on a loose field (the 0.02 rule)
    loose: dict[str, float] = field(default_factory=dict)  # every loose field's difference
    max_tight: float = 0.0  # largest relative difference seen on an exact-rule float

    @property
    def ok(self) -> bool:
        return not self.mismatches


def _is_loose(path: str) -> str | None:
    """``abs`` or ``rel`` when ``path`` names a loose field, else None."""
    parts = path.split("/")
    if parts[-1] in ("auc_roc", "accuracy"):
        return "abs"
    if "gmm_fits" in parts:
        return "abs" if parts[-1] == "weights" else "rel" if parts[-1] != "n_components" else None
    if "top_features" in parts:
        return "abs"
    return None


def compare(
    shape: Any,
    base: Any,
    path: str = "",
    diff: Diff | None = None,
    spread: dict[str, tuple[float, float]] | None = None,
) -> Diff:
    """Compare Shape's output with the baseline's (see the module docstring); returns the Diff.

    ``spread`` maps the path of a field that depends on the baseline's process (the adversarial
    test's, which follows ``PYTHONHASHSEED``) to the range the baseline itself covers across seeds:
    Shape's value is accepted within 0.02 of the baseline's or inside that range."""
    d = diff if diff is not None else Diff()
    if isinstance(base, dict):
        if not isinstance(shape, dict):
            d.mismatches.append(f"{path}: expected an object, got {type(shape).__name__}")
            return d
        for k, v in base.items():
            if k in IGNORED_KEYS:
                continue
            if k not in shape:
                d.mismatches.append(f"{path}/{k}: missing in Shape")
                continue
            compare(shape[k], v, f"{path}/{k}", d, spread)
        return d
    if isinstance(base, list):
        if not isinstance(shape, list) or len(shape) != len(base):
            d.mismatches.append(f"{path}: length {_len(shape)} != {len(base)}")
            return d
        if path.endswith("/top_features"):
            return _top_features(shape, base, path, d, spread)
        for i, (s, b) in enumerate(zip(shape, base, strict=True)):
            compare(s, b, f"{path}/{i}", d, spread)
        return d
    d.compared += 1
    if base is None or shape is None:
        if base is not shape:
            d.mismatches.append(f"{path}: {shape!r} != {base!r}")
        return d
    if isinstance(base, bool) or isinstance(base, str) or isinstance(shape, (bool, str)):
        if shape != base:
            d.mismatches.append(f"{path}: {shape!r} != {base!r}")
        return d
    kind = _is_loose(path)
    s, b = float(shape), float(base)
    diff_abs = abs(s - b)
    if kind == "abs":
        d.max_loose = max(d.max_loose, diff_abs)
        d.loose[path] = diff_abs
        if diff_abs > LOOSE and not _inside(spread, path, s):
            d.mismatches.append(f"{path}: {s!r} vs {b!r} (|diff| {diff_abs:.4g} > {LOOSE})")
    elif kind == "rel":
        rel = diff_abs / max(abs(s), abs(b), 1.0)
        d.max_loose = max(d.max_loose, rel)
        d.loose[path] = rel
        if rel > LOOSE:
            d.mismatches.append(f"{path}: {s!r} vs {b!r} (relative {rel:.4g} > {LOOSE})")
    else:
        if isinstance(base, int) and isinstance(shape, int):
            if shape != base:
                d.mismatches.append(f"{path}: {shape!r} != {base!r}")
            return d
        rel = diff_abs / max(abs(s), abs(b), ABS / REL)
        d.max_tight = max(d.max_tight, rel if diff_abs > ABS else 0.0)
        if diff_abs > ABS and rel > REL:
            d.mismatches.append(f"{path}: {s!r} vs {b!r} (relative {rel:.3g} > {REL})")
    return d


def _inside(spread: dict[str, tuple[float, float]] | None, path: str, value: float) -> bool:
    """``value`` lies in the baseline's own range for ``path`` (always false without one)."""
    if not spread or path not in spread:
        return False
    lo, hi = spread[path]
    return lo <= value <= hi


def _len(x: Any) -> Any:
    return len(x) if isinstance(x, list) else type(x).__name__


def _top_features(
    shape: list[Any],
    base: list[Any],
    path: str,
    d: Diff,
    spread: dict[str, tuple[float, float]] | None = None,
) -> Diff:
    """Feature names as sets of the larger-than-noise entries, importances within LOOSE per name."""
    sm = {n: v for n, v in shape}
    bm = {n: v for n, v in base}
    d.compared += 1
    for n in sorted(set(sm) & set(bm)):
        diff = abs(sm[n] - bm[n])
        d.max_loose = max(d.max_loose, diff)
        d.loose[f"{path}/{n}"] = diff
        if diff > LOOSE and not _inside(spread, f"{path}/{n}", sm[n]):
            d.mismatches.append(f"{path}/{n}: importance {sm[n]!r} vs {bm[n]!r}")
    only = (
        (set(sm) ^ set(bm)) - {n for n in sm if sm[n] <= LOOSE} - {n for n in bm if bm[n] <= LOOSE}
    )
    if only:
        d.mismatches.append(f"{path}: features differ beyond ties: {sorted(only)}")
    return d
