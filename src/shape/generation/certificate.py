"""Quantitative fidelity scoring and certificates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite

from shape.capture import capture_rows
from shape.profile.advanced import pearson


@dataclass(frozen=True, slots=True)
class MetricScore:
    path: str
    score: float
    tolerance: float
    passed: bool
    reference: object
    observed: object


@dataclass(frozen=True, slots=True)
class FidelityCertificate:
    level: str
    passed: bool
    score: float
    metrics: tuple[MetricScore, ...]
    generated_rows: int

    def to_dict(self):
        return asdict(self)


def _relative(a, b):
    if a is None or b is None:
        return 0.0 if a == b else 1.0
    try:
        a = float(a)
        b = float(b)
        if not (isfinite(a) and isfinite(b)):
            return 0.0 if a == b else 1.0
        return abs(a - b) / max(abs(a), abs(b), 1e-12)
    except Exception:
        return 0.0 if a == b else 1.0


_CAPTURE_KINDS = ("numeric", "text")


def _check_capture(reference_shape):
    """``certify`` reads capture documents (``shape capture``): every column has a ``kind``. A
    profile shares only a few keys with a capture, so it would be compared on almost nothing."""
    columns = reference_shape.get("columns", {})
    if not isinstance(columns, dict):
        raise ValueError("the reference is not a capture document: its columns must be a mapping")
    for name, col in columns.items():
        if not isinstance(col, dict) or col.get("kind") not in _CAPTURE_KINDS:
            raise ValueError(
                f"the reference is not a capture document (column {name!r} has no kind "
                f"{' or '.join(_CAPTURE_KINDS)}): certify reads `shape capture` output; compare "
                "a profile with data through `shape fidelity` on the data, or with `shape diff`"
            )


def certify(reference_shape, generated_rows, level="gold", tolerance=0.10, correlations=()):
    """Capture generated rows and score them against the requested evidence target."""
    _check_capture(reference_shape)
    if correlations:
        rows = list(generated_rows)
        observed = capture_rows(rows).to_dict()
    else:
        rows = None
        observed = capture_rows(generated_rows).to_dict()
    metrics = []
    for field, ref in sorted(reference_shape.get("columns", {}).items()):
        obs = observed.get("columns", {}).get(field)
        if obs is None:
            metrics.append(MetricScore(f"columns.{field}", 0.0, tolerance, False, ref, None))
            continue
        for m in ("null_count", "distinct_estimate", "mean", "min", "max", "q25", "q50", "q75"):
            if m in ref:
                e = _relative(ref.get(m), obs.get(m))
                metrics.append(
                    MetricScore(
                        f"columns.{field}.{m}",
                        max(0, 1 - e),
                        tolerance,
                        e <= tolerance,
                        ref.get(m),
                        obs.get(m),
                    )
                )
        # Compare bounded heavy-hitter distributions when both Shapes expose top-k evidence.
        if ref.get("topk") is not None and obs.get("topk") is not None:

            def probs(items):
                d = {str(x[0]): float(x[1]) for x in items}
                total = sum(d.values()) or 1.0
                return {k: v / total for k, v in d.items()}

            a, b = probs(ref["topk"]), probs(obs["topk"])
            keys = set(a) | set(b)
            tv = 0.5 * sum(abs(a.get(k, 0) - b.get(k, 0)) for k in keys)
            metrics.append(
                MetricScore(
                    f"columns.{field}.topk_distribution",
                    max(0, 1 - tv),
                    tolerance,
                    tv <= tolerance,
                    a,
                    b,
                )
            )
    for left, right, target in correlations:
        got = pearson(rows, left, right)
        e = abs(got - target) / 2
        metrics.append(
            MetricScore(
                f"correlation.{left}.{right}", max(0, 1 - e), tolerance, e <= tolerance, target, got
            )
        )
    if not metrics:  # a reference that describes nothing certifies nothing
        score, passed = 0.0, False
    else:
        score = sum(x.score for x in metrics) / len(metrics)
        passed = all(x.passed for x in metrics)
    return FidelityCertificate(
        level,
        passed,
        score,
        tuple(metrics),
        len(rows) if rows is not None else int(observed.get("rows", 0)),
    )
