import pytest

from shape.errors import ShapeSecurityError
from shape.policy import authorize_release
from shape.privacy import assess_summary
from shape.security import Sensitivity


def test_rare_values_block_lower_trust_release():
    report = assess_summary({"count": 100, "distinct_estimate": 99, "topk": [("rare", 1, 0)]})
    with pytest.raises(ShapeSecurityError):
        authorize_release(
            Sensitivity(categories=frozenset({"PII"})), Sensitivity(), True, report, "APR-1"
        )
