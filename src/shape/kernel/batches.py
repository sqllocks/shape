"""Arrow RecordBatch kernel contracts."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING

from shape.errors import ShapeSchemaError

if TYPE_CHECKING:
    import pyarrow as pa


def validate_batch(batch: pa.RecordBatch) -> None:
    import pyarrow as pa

    if not isinstance(batch, pa.RecordBatch):
        raise ShapeSchemaError(f"Expected pyarrow.RecordBatch, got {type(batch).__name__}")


def iter_batches(source: Iterable[pa.RecordBatch]) -> Iterator[pa.RecordBatch]:
    """Validate and yield one batch at a time; never materializes the source."""
    for batch in source:
        validate_batch(batch)
        yield batch
