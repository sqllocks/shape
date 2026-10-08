import pytest

from shape.security import Sensitivity

pytestmark = pytest.mark.security


def test_join_never_drops_restrictions():
    left = Sensitivity(categories=frozenset({"PHI"}), restrictions=frozenset({"LOCAL_ONLY"}))
    right = Sensitivity(classifications=frozenset({"TOP_SECRET"}))
    out = left.join(right)
    assert out.dominates(left)
    assert out.dominates(right)
