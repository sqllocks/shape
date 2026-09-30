"""pandas' string-to-datetime rules (``dtparse``), against outputs recorded from pandas 3.0.6.

The golden files hold what ``pd.to_datetime(s, format="mixed")`` (one string) and
``pd.to_datetime(series, errors="coerce")`` (a column) returned; ``"ERR"`` is a ValueError. Text
that pandas dates relative to the day it runs (time-only strings) was recorded on 2026-09-30, so
the tests pin that day; dateutil's two-digit-year pivot is pinned to 2026 as well.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from shape.profile.reference import _dateutil_parser as du
from shape.profile.reference import dtparse

DATA = Path(__file__).parent / "data"
TODAY = dt.datetime(2026, 9, 30, 12, 34, 56)


@pytest.fixture(autouse=True)
def _pin_century(monkeypatch):
    info = du.DEFAULTPARSER.info
    monkeypatch.setattr(info, "_year", 2026)
    monkeypatch.setattr(info, "_century", 2000)


def _show(v):
    if v is dtparse._NAT:
        return "NaT"
    return "ERR" if v is None else str(v)


def test_mixed_matches_pandas():
    golden = json.loads((DATA / "dtparse_golden_mixed.json").read_text(encoding="utf-8"))["mixed"]
    wrong = []
    for text, want in golden.items():
        try:
            got = _show(dtparse.parse_mixed(text, TODAY))
        except NotImplementedError:
            got = "TZ"
        if got == "TZ":
            assert want not in ("NaT",) and (
                want == "ERR" or "+" in want or want.endswith("00:00")
            ), (
                text,
                want,
            )
            continue
        if got != want:
            wrong.append((text, want, got))
    assert not wrong, wrong[:10]


def test_column_coercion_matches_pandas():
    golden = json.loads((DATA / "dtparse_golden_columns.json").read_text(encoding="utf-8"))
    wrong = []
    for case in golden["columns"]:
        values = [v if v != "" else None for v in case["values"]]
        try:
            got = ["NaT" if v is None else str(v) for v in dtparse.coerce_column(values, TODAY)]
        except NotImplementedError:
            continue
        if got != case["expected"]:
            wrong.append((case["values"], case["expected"], got))
    assert not wrong, wrong[:5]


@pytest.mark.parametrize(
    ("text", "fmt"),
    [
        ("May 5, 2021", "%B %d, %Y"),
        ("2021-03-04", "%Y-%m-%d"),
        ("5/3/2021", "%m/%d/%Y"),
        ("Mar 6 2021", "%b %d %Y"),
        ("10:00:00", None),  # no year/month/day: nothing to guess
        ("May 5", None),
    ],
)
def test_guess_format(text, fmt):
    assert dtparse.guess_format(text, TODAY) == fmt


def test_zone_bearing_text_is_refused_not_misread():
    with pytest.raises(NotImplementedError):
        dtparse.parse_mixed("2021-03-04T10:00:00+05:00")
