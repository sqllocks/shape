"""W5-02 item 4: the same input always gives byte-identical output."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from shape.design import DesignInput
from shape.design.ddl import DIALECTS, emit_ddl
from shape.design.engine import MODES, derive
from shape.design.lint import lint


def render(doc: dict[str, Any], mode: str, dialect: str) -> str:
    design = DesignInput.from_dict(doc)
    result = derive(design, mode)
    findings = [f.to_dict() for f in lint(design, mode)]
    return (
        emit_ddl(result, dialect)
        + json.dumps(result.to_dict(), sort_keys=True)
        + json.dumps(findings, sort_keys=True)
    )


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("dialect", DIALECTS)
def test_two_runs_are_byte_identical(doc: dict[str, Any], mode: str, dialect: str) -> None:
    assert render(doc, mode, dialect).encode() == render(doc, mode, dialect).encode()


def test_key_order_in_the_json_does_not_matter(doc: dict[str, Any]) -> None:
    shuffled = json.loads(json.dumps(doc, sort_keys=True))
    assert render(doc, "star", "tsql") == render(shuffled, "star", "tsql")


def test_output_does_not_depend_on_the_hash_seed(tmp_path: Path, doc: dict[str, Any]) -> None:
    # Set iteration order changes with PYTHONHASHSEED; the output must not.
    src = tmp_path / "d.json"
    src.write_text(json.dumps(doc), encoding="utf-8")
    code = (
        "import sys, json\n"
        "from shape.design import load_design\n"
        "from shape.design.engine import derive\n"
        "from shape.design.ddl import emit_ddl\n"
        "d = load_design(sys.argv[1])\n"
        "for mode in ('3nf', 'star', 'snowflake'):\n"
        "    print(emit_ddl(derive(d, mode), 'postgres'))\n"
    )
    outputs = set()
    for seed in ("0", "1", "12345", "random"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        done = subprocess.run(
            [sys.executable, "-c", code, str(src)],
            capture_output=True,
            env=env,
            check=True,
            timeout=120,
        )
        outputs.add(done.stdout)
    assert len(outputs) == 1
