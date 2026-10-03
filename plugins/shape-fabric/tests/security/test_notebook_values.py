"""AUD-security2 #284: generate_notebook() never writes a caller's value into notebook code
where it could become code."""

from __future__ import annotations

import pytest
from shape_fabric.notebook import generate_notebook


@pytest.mark.parametrize(
    "domain",
    [
        "retail{__import__('os').system('id')}",  # an f-string expression in the lakehouse cell
        "retail'; import os; os.system('id'); x='",  # a quote in the csv cell
        "retail\nimport os",
        "../retail",
    ],
)
@pytest.mark.parametrize("target", ["lakehouse", "csv"])
def test_a_domain_that_is_not_a_plain_name_is_refused(domain, target):
    with pytest.raises(ValueError, match="domain"):
        generate_notebook(domain, output_target=target)


@pytest.mark.parametrize("seed", ["0); __import__('os').system('id'", 1.5, True, None])
def test_a_seed_that_is_not_an_integer_is_refused(seed):
    with pytest.raises(ValueError, match="seed"):
        generate_notebook("retail", seed=seed)


@pytest.mark.parametrize("version", ["1 -q\n!id #", "1; id", "1 os"])
def test_a_version_that_is_not_a_version_is_refused(version):
    with pytest.raises(ValueError, match="version"):
        generate_notebook("retail", version=version)


def test_plain_values_are_still_written_as_before():
    nb = generate_notebook("retail", "small", 7, "csv", version="0.9.0.dev3")
    code = "".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code")
    assert "sqllocks-shape==0.9.0.dev3 " in code
    assert "shape.generate('retail', scale='small', seed=7)" in code
    assert "output_dir = './shape_retail'" in code
