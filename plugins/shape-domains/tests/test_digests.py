"""The recorded digests of the packaged schemas: a schema is handed to the host as checked only
when its content is the content that was checked."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from shape_domains import _digests, _packaged

from shape.generation.domains import domain_names, load_domain
from shape.generation.schema import GenSchema, GenSchemaError, schema_problems
from shape.plugins.api import v1

ROOT = Path(__file__).resolve().parents[3]


def test_the_digest_file_is_up_to_date() -> None:
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "update_domain_digests.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr


def test_every_packaged_schema_is_recorded_and_valid() -> None:
    expected = {f"{d}/{f}" for d in domain_names() for f in _packaged._FILES.values()}
    assert set(_digests.SCHEMAS) == expected
    for name in domain_names():
        for mode in ("3nf", "star"):
            document = _packaged.schema_document(name, mode)
            assert schema_problems(document) == []
            assert _packaged.is_validated(name, mode)
            assert load_domain(name, mode=mode).definition.validated is True


def test_changed_content_is_checked_again(monkeypatch: pytest.MonkeyPatch) -> None:
    real = _packaged._read_bytes

    def edited(relative: str) -> bytes:
        content = real(relative)
        return content + b"\n" if relative.endswith("hr/schema.json") else content

    _packaged._schema_entry.cache_clear()
    monkeypatch.setattr(_packaged, "_read_bytes", edited)
    try:
        assert not _packaged.is_validated("hr", "3nf")
        assert _packaged.is_validated("hr", "star")
    finally:
        monkeypatch.undo()
        _packaged._schema_entry.cache_clear()
    assert _packaged.is_validated("hr", "3nf")


def test_another_json_schema_means_every_schema_is_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_digests, "JSON_SCHEMA", "0" * 64)
    _packaged._schema_entry.cache_clear()
    try:
        assert not _packaged.is_validated("hr", "3nf")
    finally:
        monkeypatch.undo()
        _packaged._schema_entry.cache_clear()


def test_a_definition_that_is_not_marked_is_still_checked() -> None:
    document = dict(_packaged.schema_document("hr", "3nf"))
    document.pop("tables")
    unmarked = v1.DomainDefinition(schema=document)
    assert unmarked.validated is False
    with pytest.raises(GenSchemaError):
        GenSchema.from_dict(unmarked.schema, validated=unmarked.validated)
