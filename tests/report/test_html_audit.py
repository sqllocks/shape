"""AUD-chaos regressions for the self-contained HTML report."""

from __future__ import annotations

import re

import pytest

from shape.report.html import _bars


@pytest.mark.parametrize("values", [[float("nan"), 1.0], [-1.0, -2.0], [float("inf"), 0.5]])
def test_bars_stay_valid_svg_inside_the_chart(values: list[float]) -> None:
    """#430: a non-finite or negative share draws an empty bar, never ``nan`` or a bar outside
    the chart."""
    svg = _bars(["a", "b"], values, width=300, height=70)
    assert "nan" not in re.sub(r"<title>.*?</title>", "", svg)
    for y, h in re.findall(
        r'<rect class="bar" x="[^"]+" y="([^"]+)" width="[^"]+" height="([^"]+)"', svg
    ):
        assert 0.0 <= float(h) <= 70.0
        assert 0.0 <= float(y) <= 70.0


def test_bars_of_ordinary_shares_are_unchanged() -> None:
    assert _bars(["a", "b"], [0.25, 0.5], width=100, height=20) == (
        '<svg viewBox="0 0 100 34" role="img"><line class="axis" x1="0" y1="20" x2="100" y2="20"/>'
        '<rect class="bar" x="0.0" y="12.0" width="49.0" height="8.0"><title>a: 0.25</title></rect>'
        '<rect class="bar" x="51.0" y="4.0" width="49.0" height="16.0"><title>b: 0.5</title></rect>'
        '<text class="lbl" x="0.0" y="31">a</text><text class="lbl" x="51.0" y="31">b</text></svg>'
    )
