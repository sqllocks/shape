"""An event too large for an event hub batch is named, wherever it falls (#356)."""

from __future__ import annotations

import pyarrow as pa
import pytest
from shape_eventhubs.testing import EmitterHarness

from shape.errors import ShapeError


@pytest.mark.parametrize("position", [0, 1])
def test_an_oversized_event_raises_a_shape_error_naming_it(position: int) -> None:
    harness = EmitterHarness(max_batch_bytes=600)
    values = ["x", "x"]
    values[position] = "y" * 2000
    batch = pa.record_batch({"_shape_table": ["t", "t"], "_shape_seq": [1, 2], "v": values})
    with pytest.raises(ShapeError, match=rf"event t/{position + 1} .*larger than an event hub"):
        harness.make().emit(harness.uri, [batch])
