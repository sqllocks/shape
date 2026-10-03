"""AUD-gen: daily batches have the declared output types (#191)."""

from __future__ import annotations

import pyarrow as pa  # type: ignore[import-untyped]
from aud_gen_fixtures import build, col

from shape.generation.batches import BatchGenerator
from shape.generation.engine import Engine


def test_a_batch_without_post_passes_has_the_declared_types():
    # 191: generate_chunk was used without Engine.finalize: decimal came out as double.
    amount = col("uniform", "decimal", low=0, high=100, output_type="decimal")
    amount["precision"], amount["scale"] = 7, 2
    s = build({"o": (["id"], {"id": col("sequence"), "amt": amount})}, {"o": 3})
    batch = BatchGenerator(s, {"o": 3}, start_date="2026-01-01").generate(1).tables["o"]
    assert batch.schema.field("amt").type == pa.decimal128(7, 2)
    whole = Engine(s, row_counts={"o": 6}).generate().tables["o"]
    assert batch["amt"].to_pylist() == whole["amt"].slice(3, 3).to_pylist()
