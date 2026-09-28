import math
import random

from shape.profile.numeric import NumericProfile


def test_numeric_merge_partition_invariant():
    vals = [random.Random(9).random() for _ in range(1000)]
    whole = NumericProfile().update(vals)
    parts = [NumericProfile().update(vals[i : i + 100]) for i in range(0, len(vals), 100)]
    merged = NumericProfile()
    for p in parts:
        merged.merge(p)
    assert (
        merged.count == whole.count
        and merged.minimum == whole.minimum
        and merged.maximum == whole.maximum
    )
    assert math.isclose(merged.mean, whole.mean, rel_tol=1e-12, abs_tol=1e-12)
    assert math.isclose(
        merged.variance_population, whole.variance_population, rel_tol=1e-10, abs_tol=1e-12
    )
