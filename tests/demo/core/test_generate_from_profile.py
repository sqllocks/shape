"""``shape.generate`` on a profile generates data (P4-08; it raised ``NotImplementedError`` before)."""

import pandas as pd
import pytest

import shape


@pytest.mark.parametrize("multi", [False, True])
def test_generate_from_a_profile_returns_the_profiles_tables(multi):
    df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "x"]})
    p = shape.profile({"t": df, "u": df} if multi else df, name=None if multi else "t")
    for obj in (p, p.to_dict()):
        result = shape.generate(obj, seed=1)
        assert set(result.tables) == ({"t", "u"} if multi else {"t"})
        for table in result.tables.values():
            assert table.num_rows == 3 and set(table.column_names) == {"a", "b"}
