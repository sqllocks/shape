"""Safe-profile parity (P7-01): the product's safe profile and validator against the pinned
baseline's, on a profiling dataset (default D2).

    source scripts/env.sh && "$SHAPE_VENV/bin/python" \
        benchmarks/vs_refengine/safe_profile_1to1/verify.py [--refresh] [d2.csv ...]

The baseline runs in its own venv (refengine_safe_dump.py, cached as JSON under
$BENCH_OUT_DIR/safe_cache keyed by the input's SHA-256); the product runs in-process on
``shape.profile`` of the same file. For each mapper configuration in VARIANTS every field of
the safe profile must match: floats within 1e-9 relative (the T-22 profile tolerance, which the
inputs already meet), everything else exactly. Set aside: ``schema_version`` and the declaration
keys (the two formats number their own history), and the named product differences below, each
checked on both sides first (the product-added keys; the value statistics of a column below its
k, #395). The validator must give the same ``(rule, path)`` findings on every safe artifact and
fixture. Exit 0 only when everything matches; 2 when an input
file is missing.
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
from paths import BENCH_OUT_DIR, PROFILE_DATA_DIR, REFENGINE_PY  # noqa: E402

CACHE = BENCH_OUT_DIR / "safe_cache"
REL = 1e-9
# Keys the product adds to the baseline's format (P7-02: cells withheld by the minimum cohort).
# Parity covers every baseline key; these are compared separately, by test.
ADDED_KEYS = {"cells_suppressed"}
# Keys the product adds to every safe column (ISS-profile, issue #2, integrated in INT-13): the
# share of values that are, and that contain, an SSN, e-mail address, card number, IP or IBAN.
# Aggregates, never values; the baseline's format has neither. They are set aside only after
# check_pii_rates has checked both sides: absent from every baseline column, and in every product
# column equal to the raw profile's rates. They are never stripped from the baseline.
PII_RATE_KEYS = ("pattern_rates", "pattern_contains_rates")
# The column validators' counts and rates (W3-12): aggregates, never a value, checked and set
# aside the same way by check_validators.
VALIDATOR_KEYS = ("validators",)
# The value statistics a column with fewer non-null rows than its k does not release (#395,
# SEC-high): the product sets them to null, the baseline releases them. check_small_cohorts checks
# both sides (the column is below k by the raw profile's counts, the product holds null, the
# baseline's mean and std are the raw profile's) before withholding these keys, and only these,
# from the baseline, with the redaction-manifest flag bounds_winsorized that follows bounds.
SMALL_COHORT_KEYS = ("mean", "std", "quantiles", "bounds", "distribution_params")
# The declaration every persisted Shape file carries (W1-01): top level only, like
# ``schema_version``, because the two formats number their own history.
DECLARATION_KEYS = ("format", "version", "shape_version", "min_shape_version")


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
    key = hashlib.sha256(path.read_bytes() + (HERE / "refengine_safe_dump.py").read_bytes())
    key.update((HERE / "fixtures.py").read_bytes())
    cached = CACHE / f"{path.name}.{key.hexdigest()[:16]}.json"
    if refresh or not cached.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [str(REFENGINE_PY), str(HERE / "refengine_safe_dump.py"), str(path), str(cached)],
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


def _strip_added(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _strip_added(v) for k, v in node.items() if k not in ADDED_KEYS}
    if isinstance(node, list):
        return [_strip_added(v) for v in node]
    return node


def _raw_columns(raw: Any) -> dict[str, dict[str, Any]]:
    """Table name -> the raw profile's column dicts, for a table or a dataset profile."""
    tables = raw["tables"] if "tables" in raw else {str(raw["name"]): raw}
    return {str(n): dict(t.get("columns") or {}) for n, t in tables.items()}


def check_pii_rates(ref: Any, got: Any, raw: Any, name: str, out: list[str]) -> Any:
    """Check PII_RATE_KEYS on both sides; return ``got`` without them (``got`` is not changed)."""
    return _check_raw_keys(ref, got, raw, name, out, PII_RATE_KEYS)


def check_validators(ref: Any, got: Any, raw: Any, name: str, out: list[str]) -> Any:
    """Check VALIDATOR_KEYS on both sides; return ``got`` without them (``got`` is not changed)."""
    return _check_raw_keys(ref, got, raw, name, out, VALIDATOR_KEYS)


def _raw_row_counts(raw: Any) -> dict[str, int | None]:
    """Table name -> the raw profile's row count, for a table or a dataset profile."""
    tables = raw["tables"] if "tables" in raw else {str(raw["name"]): raw}
    return {str(n): t.get("row_count") for n, t in tables.items()}


def check_small_cohorts(
    ref: Any, got: Any, raw: Any, cfg: Any, unsafe: bool, name: str, out: list[str]
) -> Any:
    """The named difference of #395: return ``ref`` with SMALL_COHORT_KEYS withheld (set to null)
    on each column below its k, after checking both sides; ``ref`` is not changed.

    A column is below k when the raw profile's non-null rows are fewer than ``cfg.column_k``
    (never in an unsafe case, ``unsafe`` or ``cfg.unsafe_full_fidelity``, which keeps the
    statistics). On such a column the product must hold every one of the keys as null, and the
    baseline's ``mean`` and ``std`` must be the raw profile's, so the withheld values are exactly
    what the product chose not to release. Both formats set the column's redaction-manifest flag
    ``bounds_winsorized`` to ``bounds is not None``; it follows the withheld ``bounds``: the
    product's must be false and the baseline's must match its own ``bounds`` before it is withheld
    (set to false) too.
    """
    from shape.privacy.cells import non_null_base

    withheld = json.loads(json.dumps(ref))
    if unsafe or cfg.unsafe_full_fidelity:
        return withheld
    raw_cols = _raw_columns(raw)
    raw_rows = _raw_row_counts(raw)
    ref_notes = (withheld.get("redaction_manifest") or {}).get("tables") or {}
    got_notes = (got.get("redaction_manifest") or {}).get("tables") or {}
    for tname, table in (got.get("tables") or {}).items():
        ref_cols = ((withheld.get("tables") or {}).get(tname) or {}).get("columns") or {}
        for cname, col in (table.get("columns") or {}).items():
            source = raw_cols.get(tname, {}).get(cname)
            if source is None:
                continue  # reported by the PII-rate and validator checks
            base = non_null_base(
                raw_rows.get(tname), source.get("null_count"), source.get("null_rate") or 0.0
            )
            if base >= cfg.column_k(cname):
                continue
            path = f"{name}.tables.{tname}.columns.{cname}"
            for key in SMALL_COHORT_KEYS:
                if key not in col or col[key] is not None:
                    out.append(f"{path}.{key}: {base} rows < k but the product releases it")
            note = (got_notes.get(tname) or {}).get(cname) or {}
            if note.get("bounds_winsorized") is not False:
                out.append(f"{path}: {base} rows < k but the manifest says bounds_winsorized")
            other = ref_cols.get(cname)
            if other is None:
                continue  # the key comparison reports a missing baseline column
            for key in ("mean", "std"):
                if key in other:
                    _close(_norm(source.get(key)), other[key], f"{path}.{key} (baseline)", out)
            ref_note = (ref_notes.get(tname) or {}).get(cname)
            if isinstance(ref_note, dict) and "bounds_winsorized" in ref_note:
                if ref_note["bounds_winsorized"] is not (other.get("bounds") is not None):
                    out.append(
                        f"{path}: the baseline's bounds_winsorized does not match its bounds"
                    )
                ref_note["bounds_winsorized"] = False
            for key in SMALL_COHORT_KEYS:
                if key in other:
                    other[key] = None
    return withheld


def _check_raw_keys(
    ref: Any, got: Any, raw: Any, name: str, out: list[str], keys: tuple[str, ...]
) -> Any:
    """``keys`` absent from every baseline column, and in every product column equal to the raw
    profile's; ``got`` without them."""
    raw_cols = _raw_columns(raw)
    for tname, table in (ref.get("tables") or {}).items():
        for cname, col in (table.get("columns") or {}).items():
            for key in keys:
                if key in col:
                    out.append(f"{name}.tables.{tname}.columns.{cname}: baseline has {key}")
    stripped = json.loads(json.dumps(got))
    for tname, table in (stripped.get("tables") or {}).items():
        for cname, col in (table.get("columns") or {}).items():
            path = f"{name}.tables.{tname}.columns.{cname}"
            source = raw_cols.get(tname, {}).get(cname)
            if source is None:
                out.append(f"{path}: not a column of the raw profile")
                continue
            for key in keys:
                if key not in col:
                    out.append(f"{path}: product lacks {key}")
                    continue
                _close(_norm(source.get(key) or None), col.pop(key) or None, f"{path}.{key}", out)
    return stripped


def _norm(doc: Any) -> Any:
    """JSON round trip, so NaN spellings and tuple/list differences cannot differ."""
    return json.loads(json.dumps(doc, default=str))


def verify(path: Path, refresh: bool) -> list[str]:
    import shape
    from shape.privacy.safe_profile import to_safe_profile
    from shape.privacy.safe_validator import SafeProfileValidator

    base = baseline(path, refresh)
    prof = shape.profile(str(path))
    raw = _norm(prof.to_dict())
    bad: list[str] = []
    mine: dict[str, Any] = {}
    for name, (cfg, unsafe) in _configs().items():
        doc = to_safe_profile(prof, cfg, unsafe_full_fidelity=unsafe).to_dict()
        mine[name] = _norm(doc)
        ref = dict(base["variants"][name])
        got = _strip_added(dict(mine[name]))
        ref.pop("schema_version", None)
        got.pop("schema_version", None)
        for key in DECLARATION_KEYS:
            got.pop(key, None)
        diffs: list[str] = []
        got = check_pii_rates(ref, got, raw, name, diffs)
        got = check_validators(ref, got, raw, name, diffs)
        ref = check_small_cohorts(ref, got, raw, cfg, unsafe, name, diffs)
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
