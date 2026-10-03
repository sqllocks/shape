"""W3-12 items 3 and 4: the packs Shape ships, their licence records and their checksums."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

import shape
from shape.generation import reference as gen_ref
from shape.reference import discover_packs, find_pack, read_manifest

ROOT = Path(shape.__file__).resolve().parents[2]
CORE = ["iso-3166-1", "iso-639-1", "iban-lengths"]
NOTICES = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
LICENCES_REDISTRIBUTABLE = {
    "CC-BY-4.0",
    "Unicode-3.0",
    "MIT",
    "CC0-1.0",
    "Apache-2.0",
    "BSD-3-Clause",
}


@pytest.fixture(autouse=True)
def _no_search_paths(monkeypatch):
    monkeypatch.delenv(gen_ref.REFERENCE_PATH_ENV, raising=False)
    gen_ref.clear_search_paths()


def _shipped():
    return [p for p in discover_packs().packs if p.origin == "shipped"]


def test_the_core_packs_ship_with_shape():
    names = {p.name for p in _shipped()}
    assert set(CORE) <= names


def test_every_shipped_pack_has_a_redistributable_licence_and_a_notice():
    packs = _shipped()
    assert packs
    for pack in packs:
        m = pack.manifest
        assert m["license"] in LICENCES_REDISTRIBUTABLE, (pack.name, m["license"])
        assert m["attribution"] in NOTICES.replace("\n", " ") or _squash(
            m["attribution"]
        ) in _squash(NOTICES), f"{pack.name}: attribution is not in THIRD_PARTY_NOTICES.md"
        assert re.search(r"https?://", m["source"]), pack.name
        assert m["retrieved"] and m["sensitivity"] == "public"


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def test_every_shipped_checksum_matches_and_the_data_loads():
    for pack in _shipped():
        for entry in pack.datasets:
            data = (pack.path / entry["file"]).read_bytes()
            assert hashlib.sha256(data).hexdigest() == entry["sha256"], (pack.name, entry["file"])
            table = pack.table(entry["name"])
            assert table.num_rows == entry["rows"] and table.column_names == entry["fields"]


def test_the_manifests_validate_against_the_schema_file():
    schema = json.loads(
        (Path(shape.__file__).parent / "schemas" / "reference-pack-v1.schema.json").read_text()
    )
    required = set(schema["required"])
    for pack in _shipped():
        assert required <= set(pack.manifest) and set(pack.manifest) <= set(schema["properties"])
        assert pack.manifest["format"] == "shape-reference-pack" and pack.manifest["version"] == 1


def test_packs_not_shipped_because_their_licence_is_not_confirmed():
    names = {p.name for p in _shipped()}
    assert "iso-4217" not in names and "iso-639" not in names
    for name in ("iso_4217", "iso_639"):
        with pytest.raises(gen_ref.DatasetNotFoundError):
            gen_ref.load_dataset(name)


# --- iso-3166-1 -------------------------------------------------------------------------------


def test_iso_3166_1_content():
    ds = gen_ref.load_dataset("iso_3166_1")
    assert ds.fields == ("alpha2", "alpha3", "numeric", "name")
    rows = {
        a2: (a3, n, name)
        for a2, a3, n, name in zip(*(ds.column(f).to_pylist() for f in ds.fields), strict=True)
    }
    assert len(rows) == len(ds) == 249
    assert rows["US"] == ("USA", "840", "United States")
    assert rows["AF"][1] == "004"  # numeric codes are three-character text, zeros kept
    assert rows["DE"][0] == "DEU" and rows["JP"][1] == "392" and rows["GB"][0] == "GBR"
    assert "XK" not in rows and "AC" not in rows and "AA" not in rows  # not ISO 3166-1 codes
    assert all(
        len(a3) == 3 and len(n) == 3 and n.isdigit() and name for a3, n, name in rows.values()
    )
    assert len({a3 for a3, _, _ in rows.values()}) == 249
    assert len({n for _, n, _ in rows.values()}) == 249


# --- iso-639-1 --------------------------------------------------------------------------------


def test_iso_639_1_content():
    ds = gen_ref.load_dataset("iso_639_1")
    assert ds.fields == ("alpha2", "name")
    rows = dict(zip(ds.column("alpha2").to_pylist(), ds.column("name").to_pylist(), strict=True))
    assert len(rows) == 183
    assert rows["en"] == "English" and rows["de"] == "German" and rows["tl"] == "Tagalog"
    assert "iw" not in rows and "sh" not in rows and "mo" not in rows  # withdrawn codes
    assert all(len(c) == 2 and c.islower() for c in rows)


# --- iban-lengths -----------------------------------------------------------------------------


def test_iban_lengths_content():
    ds = gen_ref.load_dataset("iban_lengths")
    assert ds.fields == ("country", "length")
    rows = dict(zip(ds.column("country").to_pylist(), ds.column("length").to_pylist(), strict=True))
    assert rows["DE"] == 22 and rows["GB"] == 22 and rows["NO"] == 15 and rows["MT"] == 31
    assert rows["FR"] == 27 and rows["CH"] == 21 and rows["SA"] == 24
    assert all(15 <= n <= 34 for n in rows.values()) and len(rows) >= 80


def test_the_notice_records_every_data_source():
    for needle in (
        "GeoNames",
        "CC-BY-4.0",
        "Unicode License",
        "schwifty",
        "Martin Domke",
    ):
        assert needle in NOTICES, needle


def test_the_documentation_covers_each_pack_and_validator():
    doc = (ROOT / "docs" / "REFERENCE_PACKS.md").read_text(encoding="utf-8")
    for pack in ("us-zip-city", "iso-3166-1", "iso-639-1", "iso-4217", "iso-639", "iban-lengths"):
        assert f"`{pack}`" in doc, pack
    for kind in (
        "iban",
        "iso3166_alpha2",
        "iso3166_alpha3",
        "iso4217",
        "iso639_1",
        "us_zip",
        "valid_as",
    ):
        assert f"`{kind}`" in doc, kind
    for word in (
        "shape reference list",
        "shape reference show",
        "--validate",
        "build_reference_packs",
    ):
        assert word in doc, word
    assert find_pack("iso-3166-1").manifest["license"] in doc
    assert read_manifest(find_pack("us-zip-city").path)["license"] in doc
