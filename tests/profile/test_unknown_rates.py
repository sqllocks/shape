"""A profile may say "unknown" (``None``) for ``null_rate``, ``cardinality_ratio`` and
``is_unique`` of a column no row was read for (a database profile with no sample). Everything
that reads profiles must take that, in the core alone (no plugin installed)."""

from __future__ import annotations

import copy

import pyarrow as pa
import pytest

import shape
from shape.privacy.safe_profile import SafeConfig, SafeProfile, to_safe_profile
from shape.profile.reference import Profile

UNKNOWN = ("null_rate", "cardinality_ratio", "is_unique")


@pytest.fixture()
def known():
    rows = 300
    return shape.profile(
        pa.table(
            {
                "id": list(range(rows)),
                "kind": [("a", "b", "c")[i % 3] for i in range(rows)],
                "amount": [float(i % 17) for i in range(rows)],
            }
        )
    )


@pytest.fixture()
def unknown(known):
    data = copy.deepcopy(known.to_dict())
    for table in data["tables"].values() if "tables" in data else [data]:
        for col in table["columns"].values():
            for field in UNKNOWN:
                col[field] = None
    return Profile(data, name="unknown")


def test_the_unknown_profile_really_holds_none(unknown):
    col = next(iter(unknown.to_dict()["columns"].values()))
    assert [col[f] for f in UNKNOWN] == [None, None, None]


def test_it_saves_and_loads(unknown, tmp_path):
    path = tmp_path / "u.shape"
    shape.save(unknown, path, capture="full")
    again = shape.load(path)
    assert again == unknown
    assert again.to_dict()["columns"]["id"]["null_rate"] is None


def test_it_renders_and_summarises(unknown):
    html = unknown.to_html()
    assert "nulls n/a" in html and "nulls 0.00%" not in html
    summary = unknown.summary()
    assert summary["columns"]["id"]["null_rate"] is None
    assert summary["columns"]["id"]["is_unique"] is None


def test_check_neither_fails_nor_passes_a_rule_it_cannot_judge(unknown, known):
    contract = {"columns": {"id": {"unique": True, "max_null_rate": 0.0}}}
    assert shape.check(unknown, contract).passed  # unknown is not a violation
    assert shape.check(known, contract).passed
    broken = {"columns": {"kind": {"unique": True}}}
    assert not shape.check(known, broken).passed  # known and false: still a violation


def test_diff_ignores_a_null_rate_it_cannot_compare(unknown, known):
    assert not shape.diff(unknown, known).drifted
    assert not shape.diff(known, unknown).drifted
    assert not shape.diff(unknown, unknown).drifted


def test_a_safe_profile_keeps_the_unknown_rate(unknown):
    safe = to_safe_profile(unknown, SafeConfig(k=2))
    cols = [c for t in safe.tables.values() for c in t.columns.values()]
    assert cols and all(c.null_rate is None for c in cols)
    assert SafeProfile.from_dict(safe.to_dict()) == safe
