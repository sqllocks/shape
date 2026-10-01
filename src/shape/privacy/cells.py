"""Small-cell suppression for released category weights."""

from __future__ import annotations

from collections.abc import Mapping

OTHER_BUCKET = "__OTHER__"


def suppress_weights(
    weights: Mapping[str, float],
    k: int,
    row_count: int | None,
    null_rate: float | None = None,
) -> tuple[dict[str, float], int]:
    """Fold every category with fewer than ``k`` rows into one ``__OTHER__`` bucket.

    ``weights`` maps category to proportion; a category's row count is
    ``round(proportion * row_count)``. Returns ``(released weights, categories folded)``. With
    no usable ``row_count``, or ``k <= 1``, nothing can be judged and the weights pass through.
    The input is never modified.
    """
    if not weights:
        return {}, 0
    if not row_count or row_count <= 0 or k <= 1:
        return dict(weights), 0
    surviving: dict[str, float] = {}
    other = 0.0
    folded = 0
    for value, weight in weights.items():
        if value == OTHER_BUCKET:
            other += float(weight)
        elif round(float(weight) * row_count) < k:
            other += float(weight)
            folded += 1
        else:
            surviving[value] = float(weight)
    if folded > 0 or other > 0.0:
        surviving[OTHER_BUCKET] = surviving.get(OTHER_BUCKET, 0.0) + other
    return surviving, folded
