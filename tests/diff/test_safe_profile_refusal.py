"""Share-safe JSON exports must never produce a successful profile diff."""

import pyarrow as pa
import pytest

import shape
from shape.privacy.safe_profile import to_safe_profile


@pytest.mark.parametrize("nulls", [6, 12])
@pytest.mark.parametrize("safe_side", ["baseline", "current", "both"])
@pytest.mark.parametrize("serialized", [False, True])
def test_diff_refuses_safe_profile(nulls, safe_side, serialized):
    baseline = shape.profile(pa.table({"value": [1] * 100}))
    current = shape.profile(pa.table({"value": [None] * nulls + [1] * (100 - nulls)}))
    assert "null_rate_change" in {c["kind"] for c in shape.diff(baseline, current).changes}
    inputs = {"baseline": baseline, "current": current}
    for side in inputs:
        if safe_side in (side, "both"):
            safe = to_safe_profile(inputs[side])
            inputs[side] = safe.to_dict() if serialized else safe
    named_side = "baseline" if safe_side == "both" else safe_side
    with pytest.raises(ValueError, match=rf"{named_side}.*safe profile.*\.shape"):
        shape.diff(inputs["baseline"], inputs["current"])
