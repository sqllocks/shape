"""``shape.profile.DatetimeProfile``: count, nulls, min and max, and merging (AUD-tests)."""

from datetime import date, datetime

from shape.profile import DatetimeProfile


def test_update_counts_values_and_nulls_and_tracks_the_range():
    p = DatetimeProfile().update([date(2024, 5, 1), None, date(2023, 1, 2), date(2024, 12, 31)])
    assert (p.count, p.null_count) == (4, 1)
    assert (p.minimum, p.maximum) == (date(2023, 1, 2), date(2024, 12, 31))


def test_only_nulls_leave_the_range_empty():
    p = DatetimeProfile().update([None, None])
    assert (p.count, p.null_count, p.minimum, p.maximum) == (2, 2, None, None)


def test_update_accumulates_across_calls_and_returns_the_profile():
    p = DatetimeProfile()
    assert p.update([datetime(2024, 1, 1)]) is p
    p.update([datetime(2023, 6, 1), datetime(2025, 1, 1)])
    assert (p.count, p.minimum, p.maximum) == (3, datetime(2023, 6, 1), datetime(2025, 1, 1))


def test_merge_equals_one_pass():
    a_vals = [date(2024, 3, 1), None]
    b_vals = [date(2020, 1, 1), date(2030, 1, 1), None]
    merged = DatetimeProfile().update(a_vals).merge(DatetimeProfile().update(b_vals))
    assert merged == DatetimeProfile().update(a_vals + b_vals)


def test_merge_with_an_empty_side_keeps_the_other_range():
    full = DatetimeProfile().update([date(2024, 1, 1)])
    assert DatetimeProfile().merge(full) == full
    assert DatetimeProfile().update([date(2024, 1, 1)]).merge(DatetimeProfile()) == full
