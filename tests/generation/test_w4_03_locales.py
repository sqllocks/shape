"""W4-03 (issue #74): basic locale packs, the ``locale`` strategy.

A locale is a country: places and postcodes (GeoNames, CC BY 4.0), phone numbers only in the
ranges a country reserves for fiction, first names where an openly licensed list is shipped. The
data is shipped by ``sqllocks-shape-domains``; every source is quoted in
``THIRD_PARTY_NOTICES.md``. No provider makes a national identifier.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

pytest.importorskip("shape_domains")

from shape.builtins.strategies import locale_pack  # noqa: E402
from shape.generation.engine import Engine  # noqa: E402
from shape.generation.schema import GenSchema  # noqa: E402
from shape.generation.strategy_kit import StrategyError  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
COUNTRIES = ("US", "CA", "GB", "DE", "FR", "IN", "AU")
POSTCODE = {
    "US": r"\d{5}",
    "CA": r"[ABCEGHJKLMNPRSTVXY]\d[A-Z] \d[ABCEGHJKLMNPRSTVWXYZ]\d",
    "GB": r"[A-Z]{1,2}\d[A-Z\d]? \d[ABDEFGHJLNPQRSTUWXYZ]{2}",
    "DE": r"\d{5}",
    "FR": r"\d{5}",
    "IN": r"\d{6}",
    "AU": r"\d{4}",
}
FR_ROOTS = ("01 99 00", "02 61 91", "03 53 01", "04 65 71", "05 36 49", "06 39 98")


def _engine(cols: dict[str, dict[str, Any]], n: int, seed: int = 1, chunk: int | None = None):
    cdefs: dict[str, Any] = {
        "id": {"name": "id", "type": "integer", "generator": {"strategy": "sequence"}}
    }
    for name, gen in cols.items():
        cdefs[name] = {"name": name, "type": "string", "generator": gen}
    doc = {
        "schema_version": 1,
        "model": {"name": "t", "seed": seed},
        "tables": {"t": {"name": "t", "primary_key": ["id"], "columns": cdefs}},
        "relationships": [],
        "generation": {"scale": "s", "scales": {"s": {"t": n}}},
    }
    kw = {"chunk_rows": chunk} if chunk else {}
    return Engine(GenSchema.from_dict(doc), seed=seed, **kw)


def _table(cols: dict[str, dict[str, Any]], n: int, **kw: Any) -> pa.Table:
    return _engine(cols, n, **kw).generate().tables["t"]


def _loc(country: str, provider: str, **extra: Any) -> dict[str, Any]:
    return {"strategy": "locale", "locale": country, "provider": provider, **extra}


@pytest.mark.parametrize("country", COUNTRIES)
def test_postcodes_cities_and_regions_have_the_countrys_shape(country):
    t = _table(
        {
            "city": _loc(country, "city"),
            "region": _loc(country, "region"),
            "postcode": _loc(country, "postcode"),
        },
        400,
    )
    rx = re.compile(POSTCODE[country])
    assert all(rx.fullmatch(v) for v in t["postcode"].to_pylist())
    assert all(v for v in t["city"].to_pylist())
    assert all(v for v in t["region"].to_pylist())
    assert len(set(t["postcode"].to_pylist())) > 20  # not a handful


@pytest.mark.parametrize("country", COUNTRIES)
def test_place_columns_of_a_row_agree(country):
    """city, region and postcode of one row belong to one place of the shipped data."""
    t = _table(
        {
            "city": _loc(country, "city"),
            "region": _loc(country, "region"),
            "postcode": _loc(country, "postcode"),
        },
        300,
    )
    places = locale_pack.places(country)
    known = set(zip(places["city"], places["region"], strict=True))
    got = set(zip(t["city"].to_pylist(), t["region"].to_pylist(), strict=True))
    assert got <= known
    prefix = {(c, r): set() for c, r in known}  # the postcode's stem is the place's postal code
    for c, r, p in zip(places["city"], places["region"], places["postal_code"], strict=True):
        prefix[(c, r)].add(p)
    for c, r, p in zip(
        t["city"].to_pylist(), t["region"].to_pylist(), t["postcode"].to_pylist(), strict=True
    ):
        assert any(p.startswith(stem) for stem in prefix[(c, r)])


@pytest.mark.parametrize("country", COUNTRIES)
def test_the_same_row_for_any_chunking_and_run(country):
    cols = {"postcode": _loc(country, "postcode"), "city": _loc(country, "city")}
    a = _table(cols, 500, chunk=500)
    b = _table(cols, 500, chunk=37)
    c = _table(cols, 500, chunk=500)
    assert a.equals(b)
    assert a.equals(c)
    assert not a.equals(_table(cols, 500, seed=2))


def test_german_places_are_not_companies():
    names = locale_pack.places("DE")["city"]
    bad = re.compile(r"\b(GmbH|mbH|AG|KG|e\.V\.|Bank|Versicherung|Postfach|Deutsche Post)\b")
    assert [n for n in names if bad.search(n)] == []


def test_each_country_ships_places_for_its_own_codes():
    for country in COUNTRIES:
        places = locale_pack.places(country)
        assert len(places["city"]) == len(places["region"]) == len(places["postal_code"])
        assert len(places["city"]) >= 1000, country
        assert set(places["country"]) == {country}


@pytest.mark.parametrize("country", ("US", "CA"))
def test_north_american_phone_numbers_are_the_555_01xx_lines(country):
    t = _table({"phone": _loc(country, "phone_number")}, 300)
    rx = re.compile(r"\+1 \(([2-9]\d\d)\) 555-01\d\d")
    values = t["phone"].to_pylist()
    assert all(rx.fullmatch(v) for v in values)


def test_french_phone_numbers_are_in_the_arcep_audiovisual_roots():
    t = _table({"phone": _loc("FR", "phone_number")}, 600)
    rx = re.compile(r"0([1-6]) (\d\d) (\d\d) (\d\d) (\d\d)")
    values = t["phone"].to_pylist()
    roots = set()
    for v in values:
        m = rx.fullmatch(v)
        assert m, v
        roots.add(" ".join(v.split(" ")[:3]))
    assert roots == set(FR_ROOTS)
    t = _table({"phone": _loc("FR", "phone_number", format="international")}, 50)
    assert all(re.fullmatch(r"\+33 [1-6] \d\d \d\d \d\d \d\d", v) for v in t["phone"].to_pylist())


@pytest.mark.parametrize("country", ("GB", "DE", "IN", "AU"))
def test_phone_numbers_are_refused_where_no_reserved_range_is_shipped(country):
    with pytest.raises(StrategyError, match="reserved"):
        _table({"phone": _loc(country, "phone_number")}, 5)


def test_french_first_names_come_from_the_shipped_list():
    t = _table({"first": _loc("FR", "first_name")}, 500)
    pool = set(locale_pack.pool("FR", "first_names"))
    assert set(t["first"].to_pylist()) <= pool
    assert len(pool) >= 500
    assert "Marie" in pool
    assert not any(n.isupper() for n in pool)


@pytest.mark.parametrize(
    ("country", "provider"),
    [("GB", "first_name"), ("DE", "last_name"), ("FR", "last_name"), ("IN", "name")],
)
def test_names_without_an_open_source_are_refused_and_say_so(country, provider):
    with pytest.raises(StrategyError, match="no openly licensed"):
        _table({"n": _loc(country, provider)}, 5)


def test_us_names_are_the_existing_pools():
    t = _table({"n": _loc("US", "first_name"), "l": _loc("US", "last_name")}, 100)
    assert all(v for v in t["n"].to_pylist()) and all(v for v in t["l"].to_pylist())


@pytest.mark.parametrize("provider", ("ssn", "national_id", "passport", "aadhaar", "nir"))
def test_there_is_no_national_identifier_provider(provider):
    for country in COUNTRIES:
        with pytest.raises(StrategyError, match="national identifier"):
            _table({"x": _loc(country, provider)}, 3)


def test_locale_spellings_and_unknown_locales():
    for spelling in ("fr_FR", "fr-FR", "FR", "fr"):
        t = _table({"p": _loc(spelling, "postcode")}, 5)
        assert all(re.fullmatch(r"\d{5}", v) for v in t["p"].to_pylist())
    with pytest.raises(StrategyError, match="locale"):
        _table({"p": _loc("ZZ", "postcode")}, 5)
    with pytest.raises(StrategyError, match="locale"):
        _table({"p": {"strategy": "locale", "provider": "postcode"}}, 5)
    with pytest.raises(StrategyError, match="provider"):
        _table({"p": _loc("FR", "nonsense")}, 5)


def test_two_groups_draw_independent_places():
    t = _table(
        {
            "home": _loc("DE", "postcode"),
            "work": _loc("DE", "postcode", group="work"),
        },
        200,
    )
    assert t["home"].to_pylist() != t["work"].to_pylist()


def test_shipped_files_match_the_manifest():
    manifest = json.loads((locale_pack.DATA_DIR / "MANIFEST.json").read_text("utf-8"))
    assert manifest["format"] == "shape-locale-data"
    assert isinstance(manifest["version"], int)
    for name, entry in manifest["files"].items():
        data = (locale_pack.DATA_DIR / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"], name
        assert entry["source"] and entry["licence"] and entry["read_on"]
    shipped = {p.name for p in locale_pack.DATA_DIR.iterdir() if p.name != "MANIFEST.json"}
    assert shipped == set(manifest["files"])


def test_notice_file_quotes_every_source_and_licence():
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text("utf-8")
    manifest = json.loads((locale_pack.DATA_DIR / "MANIFEST.json").read_text("utf-8"))
    for entry in manifest["files"].values():
        assert entry["source"] in notices, entry["source"]
        assert entry["licence_text_url"] in notices
    assert "Licence Ouverte" in notices
    assert "Decision n° 2018-0881" in notices or "2018-0881" in notices


def test_the_docs_describe_every_locale_and_the_gaps():
    doc = (ROOT / "docs" / "LOCALES.md").read_text("utf-8")
    for country in COUNTRIES:
        assert f"`{country}`" in doc
    for word in ("reserved", "national identifier", "no openly licensed"):
        assert word in doc
