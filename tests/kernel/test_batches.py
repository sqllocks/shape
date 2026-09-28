import pyarrow as pa
import pytest

from shape.errors import ShapeSchemaError
from shape.kernel import iter_batches, validate_batch


def test_record_batch_is_canonical_boundary():
    batch = pa.record_batch({"x": [1, 2, 3]})
    validate_batch(batch)


def test_non_batch_rejected():
    with pytest.raises(ShapeSchemaError):
        validate_batch(pa.table({"x": [1]}))


def test_iteration_is_lazy():
    seen = []

    def source():
        for i in range(3):
            seen.append(i)
            yield pa.record_batch({"x": [i]})

    it = iter_batches(source())
    next(it)
    assert seen == [0]
