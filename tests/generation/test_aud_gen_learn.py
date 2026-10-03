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
