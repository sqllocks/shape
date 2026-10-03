"""AUD-security2 #300: the COPY INTO location check takes the whole value (``$`` with
``re.match`` let a trailing newline through)."""

from __future__ import annotations

import pytest
from shape_fabric.warehouse import copy_literal

from shape.errors import ShapeError


def test_a_copy_location_with_a_trailing_newline_is_refused():
    with pytest.raises(ShapeError, match="staging location"):
        copy_literal("https://acct.dfs.core.windows.net/c/x.parquet\n")
    assert copy_literal("https://acct.dfs.core.windows.net/c/x.parquet") == (
        "'https://acct.dfs.core.windows.net/c/x.parquet'"
    )
