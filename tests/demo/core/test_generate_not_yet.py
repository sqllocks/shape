"""shape.generate on a 0.9 profile fails clearly instead of returning placeholders."""

import pandas as pd
import pytest

import shape


@pytest.mark.parametrize("multi", [False, True])
def test_generate_from_a_profile_is_not_available_yet(multi):
    df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "x"]})
    p = shape.profile({"t": df, "u": df} if multi else df, name=None if multi else "t")
    for obj in (p, p.to_dict()):
        with pytest.raises(NotImplementedError, match="not available yet"):
            shape.generate(obj)
