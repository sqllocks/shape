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
