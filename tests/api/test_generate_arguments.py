"""#251: `shape.generate` refuses an argument the form it takes cannot use, instead of ignoring
it (a result of another size than asked for)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import shape

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[2] / "docs/bridge/vectors/fixtures/schema.json").read_text()
)
EVIDENCE = {"rows": 20, "columns": {"x": {"kind": "numeric", "mean": 0.0, "std": 1.0}}}


@pytest.mark.parametrize(
    "target, kwargs, named",
    [
        ("retail", {"n": 10}, "n"),
        ("retail", {"relationships": []}, "relationships"),
        (SCHEMA, {"n": 10}, "n"),
        (SCHEMA, {"mode": "star"}, "mode"),
        (SCHEMA, {"relationships": []}, "relationships"),
        (EVIDENCE, {"n": 5, "scale": "large"}, "scale"),
        (EVIDENCE, {"n": 5, "mode": "star"}, "mode"),
    ],
)
def test_an_argument_the_form_does_not_use_is_refused(target, kwargs, named):
    with pytest.raises(TypeError, match=rf"\b{named}\b"):
        shape.generate(target, **kwargs)


def test_a_profile_refuses_relationships_and_mode(tmp_path):
    src = tmp_path / "t.csv"
    src.write_text("id,v\n" + "".join(f"{i},{i % 7}\n" for i in range(50)))
    prof = shape.profile(str(src))
    for kwargs, named in (({"relationships": []}, "relationships"), ({"mode": "star"}, "mode")):
        with pytest.raises(TypeError, match=named):
            shape.generate(prof, **kwargs)
    assert shape.generate(prof, n=30, seed=1).tables["t"].num_rows == 30


def test_the_arguments_each_form_uses_still_work():
    assert shape.generate(SCHEMA, seed=2).tables
    star = shape.generate("retail", scale="small", seed=3, mode="star")
    assert star.tables
    columns, _report = shape.generate(EVIDENCE, 5, seed=1)
    assert len(columns["x"]) == 5
