"""GeoNames postal and Census Gazetteer loaders: filtering, bad rows and provenance (AUD-tests)."""

import hashlib

import pytest

from shape.location import Location
from shape.location.reference import (
    load_census_gazetteer,
    load_geonames_postal,
    sha256_file,
)

GEONAMES = (
    "US\t43215\tColumbus\tOhio\tOH\tFranklin\t049\t\t\t39.965\t-83.001\t4\n"
    "CA\tK1A\tOttawa\tOntario\tON\t\t\t\t\t45.4\t-75.7\t6\n"
    "US\t00000\t\tNowhere\t\t\t\t\t\tbad\t-1\t1\n"
    "US\tshort\trow\n"
)


def test_geonames_filters_by_country_case_insensitively(tmp_path):
    p = tmp_path / "all.txt"
    p.write_text(GEONAMES, encoding="utf-8")
    rows, _ = load_geonames_postal(p, country="us")
    assert [r.postal_code for r in rows] == ["43215", "00000"]
    every, _ = load_geonames_postal(p)
    assert [r.country for r in every] == ["US", "CA", "US"]  # the short row is dropped


def test_geonames_row_fields_and_a_bad_coordinate(tmp_path):
    p = tmp_path / "all.txt"
    p.write_text(GEONAMES, encoding="utf-8")
    rows, prov = load_geonames_postal(p)
    columbus, ottawa, nowhere = rows
    assert columbus == Location(
        country="US",
        state="OH",
        county="Franklin",
        city="Columbus",
        postal_code="43215",
        canonical_id="geonames:postal:US:43215:Columbus:OH",
        latitude=39.965,
        longitude=-83.001,
    )
    assert ottawa.county is None and ottawa.state == "ON"
    assert nowhere.state == "Nowhere"  # no admin1 code: the admin1 name
    assert nowhere.city is None and nowhere.latitude is None and nowhere.longitude is None
    assert prov.sha256 == hashlib.sha256(p.read_bytes()).hexdigest()
    assert (prov.source, prov.version) == ("GeoNames Postal Code Dataset", "downloaded")


def test_sha256_file_reads_large_files_in_blocks(tmp_path):
    p = tmp_path / "big.bin"
    data = bytes(range(256)) * 9000  # more than one 1 MiB block
    p.write_bytes(data)
    assert sha256_file(p) == hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize(
    "kind, field, value",
    [
        ("zcta", "postal_code", "43215"),
        ("county", "county", "Franklin County"),
        ("place", "city", "Franklin County"),
        ("state", "state", "Franklin County"),
    ],
)
def test_census_kinds_fill_their_field(tmp_path, kind, field, value):
    p = tmp_path / "gaz.txt"
    p.write_text(
        "GEOID\tNAME\tINTPTLAT\tINTPTLONG\n43215\tFranklin County\t39.9\t-83.0\n",
        encoding="utf-8",
    )
    (row,), prov = load_census_gazetteer(p, kind, "2024")
    assert getattr(row, field) == value
    assert row.canonical_id == f"census:2024:{kind}:43215"
    assert (row.country, row.latitude, row.longitude) == ("US", 39.9, -83.0)
    assert (prov.source, prov.version, prov.license_id) == (
        "US Census Gazetteer",
        "2024",
        "PUBLIC-DOMAIN-USG",
    )


def test_census_pipe_delimited_with_bom_and_padded_headers(tmp_path):
    p = tmp_path / "gaz.txt"
    p.write_text(
        "﻿GEOID2 | NAME2 |INTPTLAT|INTPTLONG \n01|Alabama||\n02|Alaska|x|1\n",
        encoding="utf-8",
    )
    rows, _ = load_census_gazetteer(p, "state", "2020")
    assert [(r.canonical_id, r.state) for r in rows] == [
        ("census:2020:state:01", "Alabama"),
        ("census:2020:state:02", "Alaska"),
    ]
    assert rows[0].latitude is None and rows[0].longitude is None  # empty
    assert rows[1].latitude is None and rows[1].longitude is None  # not a number


def test_census_unknown_kind_is_refused(tmp_path):
    # an unknown kind used to give records that carry only the id; #380 refuses it (AUD-pluginfw)
    p = tmp_path / "gaz.txt"
    p.write_text("GEOID\tNAME\n7\tX\n", encoding="utf-8")
    with pytest.raises(ValueError, match="kind must be one of zcta, county, place, state"):
        load_census_gazetteer(p, "tract", "2024")
