import pytest

from shape.compiler import compile_plan
from shape.errors import ShapeCapabilityError


def test_missing_capability_fails_before_execution():
    with pytest.raises(ShapeCapabilityError):
        compile_plan("generate", {"numeric"}, {"numeric", "location"})


def test_plan_compiles():
    assert compile_plan("generate", {"numeric"}, {"numeric"}).steps[-1] == "validate_fidelity"
