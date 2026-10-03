"""Files the Fabric plugin writes (notebooks, ``.bim`` models, recorded tapes) are byte-identical
on every platform."""

from __future__ import annotations

import json

import pytest
from shape_fabric import recording
from shape_fabric.notebook import generate_notebook, save_notebook
from shape_fabric.semantic_model import SemanticModelExporter

from shape.generation.domains import load_domain

pytestmark = pytest.mark.contract


def test_saved_notebook_has_lf_line_ends_on_windows(tmp_path, windows_text_io):
    nb = generate_notebook("retail")
    path = save_notebook(nb, tmp_path / "n.ipynb")
    raw = path.read_bytes()
    assert b"\r" not in raw and json.loads(raw) == nb


def test_exported_model_has_lf_line_ends_on_windows(tmp_path, windows_text_io):
    path = SemanticModelExporter().export_bim(
        load_domain("retail").schema, output_path=tmp_path / "model.bim"
    )
    raw = path.read_bytes()
    assert b"\r" not in raw and json.loads(raw)["compatibilityLevel"] == 1604


def test_recorded_tape_has_lf_line_ends_on_windows(tmp_path, windows_text_io):
    path = tmp_path / "tape.json"
    recording.save(path, {"format": recording.FORMAT, "note": "Zoë"})
    raw = path.read_bytes()
    assert raw.endswith(b"}\n") and b"\r" not in raw
    assert recording.load(path)["note"] == "Zoë"
