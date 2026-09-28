from shape.location.reference import load_geonames_postal


def test_ref(tmp_path):
    p = tmp_path / "US.txt"
    p.write_text("US\t43215\tColumbus\tOhio\tOH\tFranklin\t049\t\t\t39.965\t-83.001\t4\n")
    rows, prov = load_geonames_postal(p)
    assert rows[0].county == "Franklin" and prov.license_id == "CC-BY-3.0"
