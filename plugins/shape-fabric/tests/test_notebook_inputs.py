"""generate_notebook refuses values that would change the generated code (#448)."""

from __future__ import annotations

import ast
from typing import Any

import pytest
from shape_fabric.notebook import generate_notebook


def _code_cells(nb: dict[str, Any]) -> list[str]:
    return [
        "".join(c["source"]) if isinstance(c["source"], list) else c["source"]
        for c in nb["cells"]
        if c["cell_type"] == "code"
    ]


@pytest.mark.parametrize(
    "domain", ["re'tail", 'x"y', "x'+__import__('os').getenv('HOME')+'", "a b", "a/b", "{x}", ""]
)
def test_a_domain_that_is_not_a_plain_name_is_refused(domain: str) -> None:
    with pytest.raises(ValueError, match="domain"):
        generate_notebook(domain, output_target="csv")


@pytest.mark.parametrize("seed", ["1); import os  #", 1.5, None, True])
def test_a_seed_that_is_not_an_integer_is_refused(seed: Any) -> None:
    with pytest.raises(ValueError, match="seed"):
        generate_notebook("retail", seed=seed)


def test_a_version_that_is_not_a_version_is_refused() -> None:
    with pytest.raises(ValueError, match="version"):
        generate_notebook("retail", version="1.0 -q; rm -rf ~")


@pytest.mark.parametrize("target", ["lakehouse", "csv", "display"])
@pytest.mark.parametrize("domain", ["retail", "financial_services", "my-domain.v2"])
def test_ordinary_names_give_code_that_parses(target: str, domain: str) -> None:
    nb = generate_notebook(domain, seed=7, output_target=target, version="0.9.0.dev1")
    for code in _code_cells(nb):
        lines = [ln for ln in code.splitlines() if not ln.startswith(("%", "display("))]
        ast.parse("\n".join(lines))
