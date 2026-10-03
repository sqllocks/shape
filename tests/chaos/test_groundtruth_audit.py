"""AUD-chaos regressions for the targeted corruptions and the ground-truth log."""

from __future__ import annotations

import numpy as np
import pyarrow as pa

from shape.chaos.groundtruth import Corruption, corrupt_tables


def test_orphan_keys_match_no_parent_row_without_declared_references() -> None:
    """#397: with no ``references`` the parent is found by the column name, so no orphan key
    is a real parent key even when the parent's keys go far above the child's."""
    parent = pa.table({"customer_id": pa.array(np.arange(1, 20_000_001, 100), pa.int64())})
    child = pa.table(
        {
            "order_id": pa.array(np.arange(1000), pa.int64()),
            "customer_id": pa.array(np.arange(1000) * 100 + 1, pa.int64()),
        }
    )
    out = corrupt_tables(
        {"customer": parent, "order": child},
        [Corruption("orphan_keys", 0.5, "order", "customer_id")],
        seed=3,
    )
    keys = set(parent.column("customer_id").to_pylist())
    assert len(out.records) == 500
    assert not [r["after"] for r in out.records if r["after"] in keys]
