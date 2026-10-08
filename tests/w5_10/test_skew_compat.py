"""W5-10: the ``shape-skew-report`` format, version 1 (a golden report written by the first release
of the format and never regenerated)."""

from __future__ import annotations

import json
from pathlib import Path

from shape import skew

GOLDEN = Path(__file__).parent / "golden" / "skew_report_v1.json"
TOP = {
    "format",
    "version",
    "scale",
    "seed",
    "tolerance",
    "within_tolerance",
    "columns",
    "skipped",
    "files",
}
COLUMN = {
    "column",
    "references",
    "parents",
    "rows",
    "top_fraction",
    "shape",
    "profile_top_share",
    "generated_top_share",
    "difference",
    "within_tolerance",
}


def test_golden_report_declares_format_and_version_1():
    doc = json.loads(GOLDEN.read_text())
    assert set(doc) == TOP
    assert doc["format"] == skew.FORMAT == "shape-skew-report"
    assert doc["version"] == skew.VERSION == 1
    assert isinstance(doc["version"], int)
    assert all(set(c) == COLUMN for c in doc["columns"])


def test_this_release_writes_the_same_layout_as_the_golden_report():
    golden = json.loads(GOLDEN.read_text())
    column = skew.ColumnResult(
        column="t.c",
        references="p.k",
        parents=10,
        rows=100,
        top_fraction=0.2,
        profile_top_share=0.8,
        generated_top_share=0.79,
        tolerance=0.02,
        shape="power",
    )
    written = skew.Rehearsal("large", 7, 0.02, [column], [], ["t.parquet"]).to_dict()
    assert set(written) == set(golden)
    assert set(written["columns"][0]) == set(golden["columns"][0])
    assert written["format"] == golden["format"] and written["version"] == golden["version"]
    assert written["columns"][0]["within_tolerance"] is True
    assert written["columns"][0]["difference"] == 0.01
