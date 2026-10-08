from shape.location.reference import load_geonames_postal


def test_ref(tmp_path):
    p = tmp_path / "US.txt"
    p.write_text("US\t43215\tColumbus\tOhio\tOH\tFranklin\t049\t\t\t39.965\t-83.001\t4\n")
    rows, prov = load_geonames_postal(p)
    assert rows[0].county == "Franklin" and prov.license_id == "CC-BY-4.0"


def test_census_county_and_place_records_carry_their_state(tmp_path):
    """#368: the USPS column of the county and place gazetteers is the record's state."""
    from shape.location import Location, LocationResolver, load_census_gazetteer

    county = tmp_path / "counties.txt"
    county.write_text(
        "USPS\tGEOID\tNAME\tINTPTLAT\tINTPTLONG\n"
        "OH\t39049\tFranklin County\t39.97\t-83.01\n"
        "PA\t42055\tFranklin County\t39.93\t-77.72\n"
    )
    rows, _ = load_census_gazetteer(county, "county", "2024")
    assert [r.state for r in rows] == ["OH", "PA"]
    hit = LocationResolver(rows).resolve(Location.county_scope("Franklin County", "OH"))
    assert hit.canonical_id == "census:2024:county:39049"
    place = tmp_path / "places.txt"
    place.write_text("USPS|GEOID|NAME|INTPTLAT|INTPTLONG\nOH|3918000|Columbus city|39.98|-82.98\n")
    (row,), _ = load_census_gazetteer(place, "place", "2024")
    assert (row.city, row.state) == ("Columbus city", "OH")


def test_census_gazetteer_refuses_an_unknown_kind(tmp_path):
    """#380: an unknown kind used to produce records that carry only an id."""
    import pytest

    from shape.location import load_census_gazetteer

    p = tmp_path / "zips.txt"
    p.write_text("GEOID\tINTPTLAT\tINTPTLONG\n43215\t39.96\t-83.0\n")
    with pytest.raises(ValueError, match="zcta"):
        load_census_gazetteer(p, "zip", "2024")


def test_census_gazetteer_sniffs_only_the_head_of_the_file(tmp_path, monkeypatch):
    """#380: the delimiter sniff must not read the whole file into memory."""
    from pathlib import Path

    from shape.location import load_census_gazetteer

    p = tmp_path / "zips.txt"
    p.write_text("GEOID\tINTPTLAT\tINTPTLONG\n" + "43215\t39.96\t-83.0\n" * 2000)

    def no_read_text(self, *args, **kwargs):
        raise AssertionError("read_text reads the whole file")

    monkeypatch.setattr(Path, "read_text", no_read_text)
    rows, _ = load_census_gazetteer(p, "zcta", "2024")
    assert len(rows) == 2000 and rows[0].postal_code == "43215"
