"""W6-04: the ``shape-data-dictionary`` format is versioned; a frozen version 1 document must
render with every later Shape, and a newer version is refused with an error that says so."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shape.dictionary import FORMAT, VERSION, render_html, render_markdown

FIXTURE = Path(__file__).parent.parent / "fixtures" / "dictionary" / "v1"


def frozen() -> dict:
    return json.loads((FIXTURE / "dictionary.json").read_text(encoding="utf-8"))


def test_declares_format_and_integer_version():
    assert FORMAT == "shape-data-dictionary"
    assert isinstance(VERSION, int) and VERSION == 1


def test_frozen_v1_renders_exactly_as_recorded():
    doc = frozen()
    assert render_markdown(doc) == (FIXTURE / "dictionary.md").read_text(encoding="utf-8")
    assert render_html(doc) == (FIXTURE / "dictionary.html").read_text(encoding="utf-8")


def test_newer_version_is_refused_with_an_upgrade_message():
    doc = {**frozen(), "version": VERSION + 1}
    with pytest.raises(ValueError, match="newer.*upgrade"):
        render_markdown(doc)


@pytest.mark.parametrize(
    "patch",
    [{"format": "other"}, {"version": "1"}, {"version": True}, {"version": 0}, {"tables": {}}],
)
def test_malformed_documents_are_refused(patch):
    with pytest.raises(ValueError):
        render_html({**frozen(), **patch})
