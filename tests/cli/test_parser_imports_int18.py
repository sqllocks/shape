"""INT-18: every command builds the full CLI parser, so the modules the merged lanes added to it
import their heavier parts only when the command that needs them runs (the stream benchmark's
T-19 check measures that start-up)."""

from __future__ import annotations

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "xml.etree.ElementTree",  # shape.cli.ci: only a JUnit report needs it
        "shape.project.changes",  # shape.cli.changes: only `shape changes` needs it
    ],
)
def test_building_the_parser_does_not_import(module: str) -> None:
    code = (
        "import importlib, sys; importlib.import_module('shape.cli.main')._build_parser(); "
        f"print({module!r} in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


def test_changes_default_name_matches_the_project_module() -> None:
    from shape.cli import changes
    from shape.project.changes import DEFAULT_NAME

    assert changes.DEFAULT_NAME == DEFAULT_NAME
