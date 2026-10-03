"""Dates in UTC ISO 8601, decimals as strings, nothing locale dependent (W1-01, issue 55, 9)."""

from __future__ import annotations

import json
import locale
import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from shape import compat
from shape.artifact import codec

SRC = Path(__file__).resolve().parents[2] / "src" / "shape"


def test_no_module_uses_the_locale() -> None:
    """Serialization must give the same bytes in every locale: nothing in Shape reads or sets it."""
    pattern = re.compile(
        r"^\s*(import locale|from locale import)|setlocale\(|locale\.(format|str|atof)"
    )
    hits = [
        f"{p.relative_to(SRC)}:{n}"
        for p in sorted(SRC.rglob("*.py"))
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert hits == []


def test_the_canonical_forms_do_not_depend_on_the_locale() -> None:
    from shape.artifact.io import canonical_json

    previous = locale.setlocale(locale.LC_ALL)
    for candidate in ("de_DE.UTF-8", "fr_FR.UTF-8", "tr_TR.UTF-8", "de_DE", "C.UTF-8"):
        try:
            locale.setlocale(locale.LC_ALL, candidate)
        except locale.Error:
            continue
        break
    try:
        value = {"x": 1234567.5, "when": compat.utc_iso(datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC))}
        assert canonical_json(value) == b'{"when":"2026-01-02T03:04:05Z","x":1234567.5}'
        assert (
            codec.dumps({"f": 0.1, "n": 10**20}, sort_keys=True)
            == b'{"f":0.1,"n":100000000000000000000}'
        )
        assert compat.json_default(Decimal("1234567.50")) == "1234567.50"
    finally:
        locale.setlocale(locale.LC_ALL, previous)


def test_the_codec_refuses_decimals_and_datetimes_instead_of_guessing() -> None:
    for value in (Decimal("1.10"), datetime(2026, 1, 1, tzinfo=UTC), {1, 2}, b"bytes"):
        with pytest.raises(TypeError):
            codec.dumps(value)


def test_a_model_artifact_refuses_a_decimal_in_its_metadata(tmp_path: Path) -> None:
    from shape.artifact import write_model

    with pytest.raises((TypeError, ValueError)):
        write_model(
            tmp_path / "a.shape", {"rows": 1, "columns": {}}, metadata={"price": Decimal("1.1")}
        )
    assert not (tmp_path / "a.shape").exists()


def test_canonical_json_refuses_non_finite_numbers() -> None:
    from shape.artifact.io import canonical_json

    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValueError):
            canonical_json({"x": bad})


def test_the_run_manifest_writes_utc_iso_and_decimals_as_strings(tmp_path: Path) -> None:
    import numpy as np

    from shape.scenario.manifest import ManifestBuilder

    b = ManifestBuilder()
    b.start(None, None, "retail", "small", 1)
    b.record_outputs(
        {
            "price": Decimal("19.90"),
            "at": datetime(2026, 10, 3, 9, 30, tzinfo=UTC),
            "count": np.int64(3),
            "ratio": np.float64(0.25),
            "dir": Path("Files") / "landing",
        }
    )
    path = tmp_path / "run.json"
    ManifestBuilder.to_file(b.finish(), path)
    doc = json.loads(path.read_text())
    assert doc["outputs"] == {
        "price": "19.90",
        "at": "2026-10-03T09:30:00Z",
        "count": 3,
        "ratio": 0.25,
        "dir": "Files/landing",
    }
    for key in ("started", "finished"):
        assert doc["timestamps"][key].endswith("Z")
        assert compat.parse_utc_iso(doc["timestamps"][key]).tzinfo is not None
    # the run id uses UTC too: it equals the start time's digits
    started = compat.parse_utc_iso(doc["timestamps"]["started"])
    assert doc["run_id"].startswith(started.strftime("%Y%m%d_%H%M%S"))


def test_the_run_manifest_does_not_str_an_unknown_object(tmp_path: Path) -> None:
    from shape.scenario.manifest import ManifestBuilder

    b = ManifestBuilder()
    b.start(None, None, "retail", "small", 1)
    b.record_outputs({"thing": object()})
    with pytest.raises(TypeError):
        ManifestBuilder.to_file(b.finish(), tmp_path / "run.json")
    with pytest.raises(TypeError, match="naive"):
        b2 = ManifestBuilder()
        b2.start(None, None, "retail", "small", 1)
        b2.record_outputs({"at": datetime(2026, 1, 1)})
        ManifestBuilder.to_file(b2.finish(), tmp_path / "run2.json")


def test_registry_entries_carry_a_utc_iso_time_beside_the_epoch(tmp_path: Path) -> None:
    from shape.registry import LocalRegistry

    reg = LocalRegistry(tmp_path)
    reg.commit("x", b"one")
    (entry,) = reg.log("x")
    assert isinstance(entry["created_at"], float)
    moment = compat.parse_utc_iso(entry["created"])
    assert abs(moment.timestamp() - entry["created_at"]) < 2


def test_old_registry_entries_without_the_iso_time_still_read(tmp_path: Path) -> None:
    from shape.registry import LocalRegistry

    reg = LocalRegistry(tmp_path)
    reg.commit("x", b"one")
    log = tmp_path / "logs" / "x.jsonl"
    entry = json.loads(log.read_text())
    entry.pop("created")
    log.write_text(json.dumps(entry) + "\n")
    assert reg.entry("x")["content_id"] == entry["content_id"]


def test_a_receipt_time_is_utc_iso(tmp_path: Path) -> None:
    from shape import migrate
    from shape.artifact import codec as c
    from shape.artifact.io import sha256, write_artifact

    body = c.dumps({"rows": 1, "columns": {}}, sort_keys=True)
    write_artifact(
        tmp_path / "a.shape",
        {"format": "shape", "format_version": 1, "name": "a", "shape_content_id": sha256(body)},
        {"shape.json": body},
    )
    result = migrate.migrate_file(tmp_path / "a.shape", tmp_path / "b.shape")
    assert result.receipt is not None
    receipt: dict[str, Any] = json.loads(result.receipt.read_text())
    assert receipt["created"].endswith("Z")
    compat.parse_utc_iso(receipt["created"])
