"""The Arrow helpers of the file-drop, SCD2, stream and workflow simulators."""

from __future__ import annotations

import datetime as dt

import pyarrow as pa
import pytest
from shape_simulation import _tables as tb


def test_as_tables_reads_mappings_results_batches_wrappers_and_frames():
    pd = pytest.importorskip("pandas")
    table = pa.table({"a": [1, 2]})

    class Wrapper:
        def to_arrow(self):
            return table

    class Result:
        tables = {"t": Wrapper()}

    out = tb.as_tables(
        {"t": table, "b": table.to_batches()[0], "w": Wrapper(), "f": pd.DataFrame({"a": [1, 2]})}
    )
    assert all(t.equals(table) for t in out.values())
    assert tb.as_tables(Result())["t"].equals(table)
    with pytest.raises(TypeError, match="mapping of table name"):
        tb.as_tables([table])
    with pytest.raises(TypeError, match="cannot read list as a table"):
        tb.as_table([1, 2])


def test_to_timestamps_understands_dates_text_dictionaries_and_nothing_else():
    day = dt.datetime(2024, 1, 2)
    assert tb.to_timestamps(pa.chunked_array([[dt.date(2024, 1, 2)]])).to_pylist() == [day]
    assert tb.to_timestamps(pa.array(["2024-01-02T00:00:00"])).to_pylist() == [day]
    mixed = tb.to_timestamps(pa.array(["2024-01-02T01:00:00+01:00", "not a date", None]))
    assert mixed.to_pylist() == [day, None, None]
    coded = pa.array(["2024-01-02"]).dictionary_encode()
    assert tb.to_timestamps(coded).to_pylist() == [day]
    assert tb.to_timestamps(pa.array([1, 2])).null_count == 2  # a number is not a date


def test_temporal_column_prefers_timestamps_then_date_named_columns():
    ts = pa.table({"x": [1], "when": pa.array([dt.datetime(2024, 1, 1)])})
    assert tb.temporal_column(ts) == "when"
    named = pa.table({"updated_count": [3], "created": ["2024-01-01"], "order_date": [None]})
    assert tb.temporal_column(named) == "created"
    dated = pa.table({"ship_date": pa.array([dt.date(2024, 1, 1)])})
    assert tb.temporal_column(dated) == "ship_date"
    assert tb.temporal_column(pa.table({"updated_count": [3]})) is None
    assert tb.temporal_column(pa.table({"date_text": ["soon"]})) is None


def test_checks_name_the_bad_setting():
    with pytest.raises(ValueError, match="Unsupported file format: 'xml'"):
        tb.check_format("xml")
    with pytest.raises(ValueError, match="unknown cadence 'weekly'"):
        tb.check_cadence("weekly")
    with pytest.raises(ValueError, match="start must be a date as YYYY-MM-DD, not '2024/01/01'"):
        tb.parse_day("2024/01/01", "start")
