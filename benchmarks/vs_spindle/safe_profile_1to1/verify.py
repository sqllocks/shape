"""Safe-profile parity (P7-01): the product's safe profile and validator against the pinned
baseline's, on a profiling dataset (default D2).

    source scripts/env.sh && "$SHAPE_VENV/bin/python" \
        benchmarks/vs_spindle/safe_profile_1to1/verify.py [--refresh] [d2.csv ...]

The baseline runs in its own venv (spindle_safe_dump.py, cached as JSON under
$BENCH_OUT_DIR/safe_cache keyed by the input's SHA-256); the product runs in-process on
``shape.profile`` of the same file. For each mapper configuration in VARIANTS every field of
the safe profile must match: floats within 1e-9 relative (the T-22 profile tolerance, which the
inputs already meet), everything else exactly, ``schema_version`` aside (the two formats number
their own history). The validator must give the same ``(rule, path)`` findings on every safe
artifact and fixture. Exit 0 only when everything matches; 2 when an input file is missing.
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from fixtures import FIXTURES  # noqa: E402
from paths import BENCH_OUT_DIR, PROFILE_DATA_DIR, SPINDLE_PY  # noqa: E402

CACHE = BENCH_OUT_DIR / "safe_cache"
REL = 1e-9


def _configs() -> dict[str, tuple[Any, bool]]:
    from shape.privacy.safe_profile import SafeConfig

    return {
        "default": (SafeConfig(), False),
        "k11": (SafeConfig(k=11), False),
        "sensitive": (SafeConfig(sensitive=True), False),
        "unsafe": (SafeConfig(), True),
        "no_pii_gate": (SafeConfig(pii_gate=False), False),
        "widen_bounds": (
            SafeConfig(bounds_lo_quantile="p0_5", bounds_hi_quantile="p99_5"),
            False,
        ),
    }


def baseline(path: Path, refresh: bool) -> dict[str, Any]:
    key = hashlib.sha256(path.read_bytes() + (HERE / "spindle_safe_dump.py").read_bytes())
    key.update((HERE / "fixtures.py").read_bytes())
    cached = CACHE / f"{path.name}.{key.hexdigest()[:16]}.json"
    if refresh or not cached.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [str(SPINDLE_PY), str(HERE / "spindle_safe_dump.py"), str(path), str(cached)],
            check=True,
        )
    data: dict[str, Any] = json.loads(cached.read_text("utf-8"))
    return data


def _close(a: Any, b: Any, path: str, out: list[str]) -> None:
    if isinstance(a, bool) or isinstance(b, bool) or a is None or b is None:
        if a != b:
            out.append(f"{path}: {a!r} != {b!r}")
    elif isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            out.append(f"{path}: keys differ by {sorted(set(a) ^ set(b))}")
            return
        for k in a:
            _close(a[k], b[k], f"{path}.{k}", out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: length {len(a)} != {len(b)}")
            return
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            _close(x, y, f"{path}[{i}]", out)
    elif isinstance(a, int | float) and isinstance(b, int | float):
        if a != b and not math.isclose(a, b, rel_tol=REL, abs_tol=1e-12):
            out.append(f"{path}: {a!r} != {b!r}")
    elif a != b:
        out.append(f"{path}: {a!r} != {b!r}")


def _norm(doc: Any) -> Any:
    """JSON round trip, so NaN spellings and tuple/list differences cannot differ."""
    return json.loads(json.dumps(doc, default=str))


def verify(path: Path, refresh: bool) -> list[str]:
    import shape
    from shape.privacy.safe_profile import to_safe_profile
    from shape.privacy.safe_validator import SafeProfileValidator

    base = baseline(path, refresh)
    prof = shape.profile(str(path))
    bad: list[str] = []
    mine: dict[str, Any] = {}
    for name, (cfg, unsafe) in _configs().items():
        doc = to_safe_profile(prof, cfg, unsafe_full_fidelity=unsafe).to_dict()
        mine[name] = _norm(doc)
        ref = dict(base["variants"][name])
        got = dict(mine[name])
        ref.pop("schema_version", None)
        got.pop("schema_version", None)
        diffs: list[str] = []
        _close(_norm(ref), got, name, diffs)
        bad += diffs[:20]
        print(f"  mapper[{name}]: {'PASS' if not diffs else f'FAIL ({len(diffs)})'}")
    validator = SafeProfileValidator()
    docs = {f"safe:{n}": v for n, v in mine.items()} | FIXTURES
    for name, doc in docs.items():
        res = validator.validate_data(_norm(doc))
        got_f = sorted([f.rule, f.path] for f in res.findings)
        ok = got_f == base["validator"][name]
        if not ok:
            bad.append(f"validator[{name}]: {got_f} != {base['validator'][name]}")
    print(f"  validator: {len(docs)} artifacts, {'PASS' if not bad else 'see mismatches'}")
    return bad


def main(argv: list[str]) -> int:
    refresh = "--refresh" in argv
    names = [a for a in argv if not a.startswith("--")] or ["d2.csv"]
    status = 0
    for n in names:
        p = PROFILE_DATA_DIR / n
        if not p.exists():
            print(f"missing {p} (generate with profile_1to1/datasets.py)", file=sys.stderr)
            return 2
        print(n)
        bad = verify(p, refresh)
        for line in bad:
            print("  MISMATCH", line)
        status |= 1 if bad else 0
    print("PARITY OK" if status == 0 else "PARITY FAILED")
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
