import pytest
from shape_fabric.testing import sample_batches


@pytest.fixture
def batches():
    return sample_batches()
