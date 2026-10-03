"""AUD-gen: schemas learned from data generate (``shape learn``, ``fit_schema``)."""

from __future__ import annotations

import datetime as dt

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.csv as pacsv  # type: ignore[import-untyped]

import shape
from shape.generation.engine import Engine
from shape.generation.learn import learn


def test_dates_written_as_text_in_another_format_generate(tmp_path):
    # 177: 01/01/1950-style dates gave temporal start '01/01/1950' and generate failed:
    # "temporal start '01/01/1950' is not an ISO date".
    days = [dt.date(1950, 1, 1) + dt.timedelta(days=37 * i) for i in range(300)]
    path = tmp_path / "dob.csv"
    pacsv.write_csv(
        pa.table({"id": list(range(300)), "dob": [d.strftime("%m/%d/%Y") for d in days]}), path
    )
    schema = learn(shape.profile(str(path)))
    gen = schema.tables["dob"].columns["dob"].generator
    for bound in ("start", "end"):
        if bound in gen:
            dt.date.fromisoformat(str(gen[bound])[:10])
    out = Engine(schema, row_counts={"dob": 200}).generate().tables["dob"]["dob"]
    values = [v for v in out.to_pylist() if v is not None]
    assert values
    assert all(dt.datetime(1949, 1, 1) <= v <= dt.datetime(1981, 1, 1) for v in values)


def test_a_new_row_count_makes_the_row_count_fields_approximate():
    # 179: fit_schema(rows=N) planned null_count and row_count as preserved, although the
    # data then has N rows and about N x null rate nulls.
    from shape.generation.fit import fit_schema

    t = pa.table(
        {"id": list(range(1000)), "a": [None if i % 5 == 0 else f"v{i % 7}" for i in range(1000)]}
    )
    plan = {
        i.evidence: i.status for i in fit_schema(shape.profile(t, name="t"), rows=10_000).plan.items
    }
    assert plan["t.a.null_count"] == "approximate"
    assert plan["t.row_count"] == "approximate"
    same = {i.evidence: i.status for i in fit_schema(shape.profile(t, name="t")).plan.items}
    assert same["t.a.null_count"] == same["t.row_count"] == "preserved"


def test_a_real_row_id_column_is_kept_and_the_surrogate_takes_another_name():
    # 180: the surrogate key overwrote the data's own _row_id (constant 5 became 1, 2, 3, ...)
    # and the plan called its minimum and uniqueness preserved.
    from shape.generation.fit import fit_schema

    t = pa.table({"_row_id": [5] * 200, "a": [i % 3 for i in range(200)]})
    fit = fit_schema(shape.profile(t, name="t"))
    columns = fit.schema.tables["t"].columns
    assert columns["_row_id"].generator["strategy"] != "sequence"
    (surrogate,) = fit.schema.tables["t"].primary_key
    assert surrogate != "_row_id" and surrogate in columns
    out = Engine(fit.schema, row_counts={"t": 50}).generate().tables["t"]
    assert set(out["_row_id"].to_pylist()) == {5}
    plan = {i.evidence: i.status for i in fit.plan.items}
    assert plan[f"t.{surrogate}"] == "approximate"


def test_zoned_timestamps_are_generated_inside_the_observed_wall_clock_range():
    # 207: bounds like '2023-12-31 19:00:00-05:00' went to the strategy, which converted them to
    # UTC: values came out to 2024-02-28 20:58, past the observed wall-clock maximum.
    import datetime as dt

    zone = dt.timezone(dt.timedelta(hours=-5))
    start = dt.datetime(2023, 12, 31, 19, tzinfo=zone)
    stamps = [start + dt.timedelta(hours=7 * i) for i in range(200)]
    t = pa.table(
        {"id": list(range(200)), "at": pa.array(stamps, pa.timestamp("us", tz="America/New_York"))}
    )
    schema = learn(shape.profile(t, name="t"))
    out = Engine(schema, row_counts={"t": 500}).generate().tables["t"]["at"].to_pylist()
    low = min(s.astimezone(zone).replace(tzinfo=None) for s in stamps)
    high = max(s.astimezone(zone).replace(tzinfo=None) for s in stamps)
    values = [v.replace(tzinfo=None) for v in out if v is not None]
    assert low <= min(values) and max(values) <= high, (min(values), max(values), low, high)
