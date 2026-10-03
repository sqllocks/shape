"""W6-03: a table whose correlated columns are fitted by the copula is written by the streaming
writer too (``write_engine`` called nothing for it, so ``shape generate --from PROFILE`` printed
"Wrote 1 files" and wrote none)."""

from __future__ import annotations

import random

import shape
from shape.generation.engine import Engine
from shape.generation.fit import PRESET, fit_schema
from shape.generation.output import write_engine


def test_a_profile_fitted_table_with_correlations_is_written(tmp_path):
    rng = random.Random(3)
    rows = []
    for _ in range(300):
        x = rng.gauss(10, 2)
        rows.append(f"{x:.3f},{x * 2 + rng.gauss(0, 0.5):.3f},{rng.choice('abc')}")
    csv = tmp_path / "t.csv"
    csv.write_text("x,y,c\n" + "\n".join(rows) + "\n")
    fitted = fit_schema(shape.profile(str(csv), name="t"), rows=40)
    assert fitted.schema.correlated_columns  # the copula applies to this table
    engine = Engine(fitted.schema, scale=PRESET, seed=1)
    files = write_engine(engine, "csv", tmp_path / "out")
    assert [p.name for p in files] == ["t.csv"]
    lines = (tmp_path / "out" / "t.csv").read_text().splitlines()
    assert len(lines) == 41 and '"x"' in lines[0]
