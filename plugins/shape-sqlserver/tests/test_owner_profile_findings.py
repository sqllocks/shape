"""A problematic sampled text column must not discard the database profile."""

from shape_sqlserver import profile_database
from shape_sqlserver.testing import FakeColumn, FakeConnection, FakeTable


def test_database_profile_records_a_zoned_text_column_problem():
    table = FakeTable(
        "events",
        [FakeColumn("id", "int"), FakeColumn("when", "varchar")],
        [(1, "2026-10-11T18:00:00+02:00"), (2, "2026-10-11T19:00:00+02:00")],
    )
    data = profile_database(connection=FakeConnection([table])).to_dict()
    columns = data["tables"]["events"]["columns"]
    assert columns["id"]["cardinality"] == 2
    assert columns["when"]["dtype"] == "string"
    assert "time zone" in columns["when"]["profiling_note"]


def test_database_profile_keeps_hour_buckets_and_other_columns():
    table = FakeTable(
        "hours",
        [FakeColumn("id", "int"), FakeColumn("HourlyBucket", "varchar")],
        [(1, "18:00-18:59"), (2, "19:00-19:59")],
    )
    columns = profile_database(connection=FakeConnection([table])).to_dict()["tables"]["hours"][
        "columns"
    ]
    assert columns["HourlyBucket"]["dtype"] == "string"
    assert columns["id"]["cardinality"] == 2
    assert "profiling_note" not in columns["HourlyBucket"]
