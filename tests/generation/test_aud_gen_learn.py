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


def test_boolean_and_integer_enums_keep_their_type(tmp_path):
    # 182: learn wrote weighted_enum without output_type: flag came out 'True'/'False' strings
    # and lvl as 0.0, 1.0 although the schema declares boolean and integer.
    path = tmp_path / "b.csv"
    pacsv.write_csv(
        pa.table(
            {
                "id": list(range(300)),
                "flag": [i % 3 == 0 for i in range(300)],
                "lvl": [i % 3 for i in range(300)],
            }
        ),
        path,
    )
    schema = learn(shape.profile(str(path)))
    out = Engine(schema, row_counts={"b": 200}).generate().tables["b"]
    assert out.schema.field("flag").type == pa.bool_()
    assert out.schema.field("lvl").type == pa.int64()
    assert set(out["lvl"].to_pylist()) <= {0, 1, 2}
