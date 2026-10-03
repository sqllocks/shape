"""W3-12 item 3: the ``us-zip-city`` pack (shipped in ``sqllocks-shape-domains``) works offline
with the joint reference checks, on the ISS2-joint example data."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import shape
from shape.cli.main import main
from shape.generation import reference as gen_ref
from shape.refpacks import find_pack

pytest.importorskip("shape_domains")


def _read_csv(path):
    import csv

    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


PAIR = {"columns": {"zip": "zip", "city": "city", "state": "state"}, "reference": "us_zip_city"}


@pytest.fixture(autouse=True)
def _no_search_paths(monkeypatch):
    monkeypatch.delenv(gen_ref.REFERENCE_PATH_ENV, raising=False)
    gen_ref.clear_search_paths()


def test_the_pack_is_found_without_any_search_path():
    pack = find_pack("us-zip-city")
    assert pack.origin == "shipped" and "shape_domains" in str(pack.path)
    m = pack.manifest
    assert m["license"] == "CC-BY-4.0"
    assert "GeoNames" in m["attribution"] and "CC-BY-4.0" in m["attribution"]
    assert "geonames.org" in m["source"] and m["retrieved"] == "2026-10-03"
    assert m["sensitivity"] == "public"


def test_zip_is_five_character_text_with_leading_zeros_kept():
    ds = gen_ref.load_dataset("us_zip_city")
    assert ds.fields == ("zip", "city", "state", "county")
    zips = ds.column("zip").to_pylist()
    assert all(isinstance(z, str) and len(z) == 5 and z.isdigit() for z in zips)
    assert len(set(zips)) == len(zips)  # one row per ZIP
    assert "00501" in zips and "02872" in zips and "90210" in zips
    assert sum(z.startswith("0") for z in zips) > 400


def test_city_state_and_county_are_filled():
    ds = gen_ref.load_dataset("us_zip_city")
    rows = {
        z: (c, s, k)
        for z, c, s, k in zip(*(ds.column(f).to_pylist() for f in ds.fields), strict=True)
    }
    assert rows["90210"] == ("Beverly Hills", "CA", "Los Angeles")
    assert rows["10001"][0] == "New York" and rows["10001"][1] == "NY"
    states = {s for _, s, _ in rows.values()}
    assert {"CA", "TX", "NY", "DC", "AK", "HI"} <= states
    assert not any(c == "" or s == "" for c, s, _ in rows.values())
    assert len(ds) > 40000


def test_the_zip_to_city_pair_matches_the_good_example_exactly(city_zip):
    prof = shape.profile(str(city_zip["good"]), reference_pairs=[PAIR])
    (pair,) = next(iter(prof.tables.values()))["joint"]["reference_pairs"]
    assert pair["reference"] == "us_zip_city"
    assert pair["match_rate"] == 1.0 and pair["mismatched"] == 0


def test_the_moved_rows_are_reported_on_the_bad_example(city_zip):
    prof = shape.profile(str(city_zip["bad"]), reference_pairs=[PAIR])
    (pair,) = next(iter(prof.tables.values()))["joint"]["reference_pairs"]
    # 8% of the ZIPs are 00000 and 5% are another place's ZIP: 13% of the 4,000 rows
    assert pair["rows"] == 4000
    assert pair["mismatched"] == 520
    assert pair["match_rate"] == pytest.approx(0.87)
    # the commonest mismatches are listed (ZIP, city, state), 00000 compared as "0"
    assert len(pair["examples"]) == 5 and all(e["rows"] >= 1 for e in pair["examples"])
    assert sum(r["zip"] == "00000" for r in _read_csv(city_zip["bad"])) == 320


def test_the_contract_rule_and_the_drift_kind_work_on_the_pack(city_zip):
    good = shape.profile(str(city_zip["good"]), reference_pairs=[PAIR])
    bad = shape.profile(str(city_zip["bad"]), reference_pairs=[PAIR])
    rule = {
        "reference_pair": [
            {
                "columns": ["zip", "city", "state"],
                "reference": "us_zip_city",
                "min_match_rate": 0.99,
            }
        ]
    }
    assert shape.check(good, rule).passed
    (v,) = shape.check(bad, rule).violations
    assert v["rule"] == "reference_pair" and v["observed"]["mismatched"] == 520
    changes = shape.diff(good, bad).changes
    assert [c for c in changes if c["kind"] == "reference_match_change"]


def test_the_command_line_works_offline_with_the_pack(city_zip, tmp_path, capsys):
    out = tmp_path / "bad.shape"
    code = main(
        [
            "profile",
            str(city_zip["bad"]),
            "-o",
            str(out),
            "--reference-pair",
            "zip,city,state=us_zip_city",
        ]
    )
    assert code == 0
    capsys.readouterr()
    (pair,) = next(iter(shape.load(str(out)).tables.values()))["joint"]["reference_pairs"]
    assert pair["mismatched"] == 520


def test_the_build_check_matches_the_shipped_files():
    """The manifest's checksum is the file's (the loader verifies it on every read)."""
    pack = find_pack("us-zip-city")
    entry = pack.dataset_entry("us_zip_city")
    assert entry is not None and entry["rows"] == len(gen_ref.load_dataset("us_zip_city"))
    assert Path(pack.path / entry["file"]).stat().st_size > 1_000_000


def test_the_json_listing_names_the_pack(capsys):
    assert main(["reference", "list", "--json"]) == 0
    packs = {p["name"]: p for p in json.loads(capsys.readouterr().out)["packs"]}
    assert packs["us-zip-city"]["datasets"][0]["name"] == "us_zip_city"
