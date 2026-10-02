"""CodeSet: date-aware validity from release masks, and the fiscal-year helpers."""

import datetime as dt

import pyarrow as pa
import pytest
from shape_healthcare_codes.model import (
    CodeSet,
    Release,
    default_releases,
    fiscal_year,
    format_icd10,
    fy_end,
    fy_start,
    normalize_code,
    releases_to_json,
)


def _table(releases: list[Release] | None) -> pa.Table:
    d = dt.date
    schema = pa.schema(
        [
            ("code", pa.string()),
            ("short_desc", pa.string()),
            ("long_desc", pa.string()),
            ("leaf", pa.bool_()),
            ("valid_from", pa.date32()),
            ("valid_to", pa.date32()),
            ("valid_mask", pa.int64()),
            ("leaf_mask", pa.int64()),
        ],
        metadata={"releases": releases_to_json(releases)} if releases else None,
    )
    return pa.table(
        {
            "code": ["A00", "A009", "B01", "C01"],
            "short_desc": ["a", "b", "c", "d"],
            "long_desc": ["a", "b", "c", "d"],
            "leaf": [False, True, True, True],
            "valid_from": [d(2016, 1, 1)] * 4,
            "valid_to": [None] * 4,
            # releases: 0, 1, 2. A00 header in all; A009 billable in 0-1 only; B01 added in 1;
            # C01 only in release 2.
            "valid_mask": [0b111, 0b011, 0b110, 0b100],
            "leaf_mask": [0b000, 0b011, 0b110, 0b100],
        },
        schema=schema,
    )


RELS = [
    Release("R0", dt.date(2020, 10, 1)),
    Release("R1", dt.date(2021, 4, 1)),
    Release("R2", dt.date(2021, 10, 1)),
]


def test_validity_is_per_release_with_exact_boundaries():
    cs = CodeSet("x", _table(RELS))
    assert not cs.is_valid("A009", dt.date(2020, 9, 30))  # before the first release
    assert cs.is_valid("A009", dt.date(2020, 10, 1))
    assert cs.is_valid("A009", dt.date(2021, 9, 30))
    assert not cs.is_valid("A009", dt.date(2021, 10, 1))  # dropped in R2
    assert not cs.is_valid("B01", dt.date(2021, 3, 31))  # added in R1
    assert cs.is_valid("B01", dt.date(2021, 4, 1))
    assert cs.is_valid("C01", dt.date(2022, 1, 1)) and not cs.is_valid("C01", dt.date(2021, 9, 30))


def test_header_codes_are_valid_but_not_billable():
    cs = CodeSet("x", _table(RELS))
    assert cs.is_valid("A00", dt.date(2021, 1, 1))
    assert not cs.is_valid("A00", dt.date(2021, 1, 1), leaf_only=True)
    assert cs.is_valid("a.009", dt.date(2021, 1, 1), leaf_only=True)  # dotted, any case


def test_codes_on_matches_is_valid():
    cs = CodeSet("x", _table(RELS))
    for day in (dt.date(2020, 10, 1), dt.date(2021, 4, 1), dt.date(2021, 12, 31)):
        for leaf in (True, False):
            got = set(cs.codes_on(day, leaf_only=leaf).to_pylist())
            want = {c for c in cs.index if cs.is_valid(c, day, leaf_only=leaf)}
            assert got == want
    assert len(cs.codes_on(dt.date(2019, 1, 1))) == 0


def test_without_release_metadata_it_is_one_release_per_fiscal_year():
    cs = CodeSet("x", _table(None))
    assert cs.releases[0].effective == dt.date(2015, 10, 1)
    assert cs.release_index(dt.date(2015, 9, 30)) == -1
    assert cs.release_index(dt.date(2016, 10, 1)) == 1


def test_lookup_and_select():
    cs = CodeSet("x", _table(RELS))
    assert "A00" in cs and "ZZZ" not in cs and len(cs) == 4
    rec = cs.get("A.009")
    assert rec is not None and rec.code == "A009" and rec.leaf
    assert cs.get("nope") is None
    assert cs.select(["C01", "A00", "missing"]).column("code").to_pylist() == ["C01", "A00"]


def test_a_table_without_base_columns_is_refused():
    with pytest.raises(ValueError, match="base columns"):
        CodeSet("x", pa.table({"code": ["A"]}))


def test_range_validity_without_masks():
    t = pa.table(
        {
            "code": ["N1"],
            "short_desc": ["n"],
            "long_desc": ["n"],
            "leaf": [True],
            "valid_from": pa.array([dt.date(2020, 1, 1)], pa.date32()),
            "valid_to": pa.array([dt.date(2020, 12, 31)], pa.date32()),
        }
    )
    cs = CodeSet("ndc", t)
    assert cs.is_valid("N1", dt.date(2020, 6, 1)) and not cs.is_valid("N1", dt.date(2021, 1, 1))
    assert len(cs.codes_on(dt.date(2020, 6, 1))) == 1 and len(cs.codes_on(dt.date(2021, 1, 1))) == 0


def test_helpers():
    assert normalize_code(" e11.9-") == "E119"
    assert format_icd10("E119") == "E11.9" and format_icd10("A00") == "A00"
    assert fiscal_year(dt.date(2026, 9, 30)) == 2026 and fiscal_year(dt.date(2026, 10, 1)) == 2027
    assert fy_start(2027) == dt.date(2026, 10, 1) and fy_end(2027) == dt.date(2027, 9, 30)
    assert default_releases(2018)[-1].id == "FY2018"
