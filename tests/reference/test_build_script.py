"""W3-12 items 3 and 4: ``scripts/build_reference_packs.py`` regenerates the packs from their
source files and checks the checksum."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest

import shape
from shape.refpacks import read_manifest

ROOT = Path(shape.__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_reference_packs.py"

GEONAMES = "\n".join(
    [
        "US\t00501\tHoltsville\tNew York\tNY\tSuffolk\t103\t\t\t40.8154\t-73.0451\t1",
        "US\t02872\tPrudence Island\tRhode Island\tRI\tNewport\t005\t\t\t41.6\t-71.3\t4",
        "US\t09720\tAPO AE\t\t\t\t\t\t\t0.0\t0.0\t",  # military: no state code
        "US\t96799\tPago Pago\tAmerican Samoa\tAS\t\t\t\t\t-14.27\t-170.7\t4",  # no county
        "US\t00501\tHoltsville Again\tNew York\tNY\tSuffolk\t103\t\t\t40.8\t-73.0\t1",  # duplicate
        "US\t9021\tShort\tCalifornia\tCA\tLos Angeles\t037\t\t\t34.0\t-118.4\t1",  # not 5 digits
    ]
)

REGION = """<?xml version="1.0" encoding="UTF-8" ?>
<supplementalData><idValidity>
  <id type='region' idStatus='regular'><!-- 4 items -->
    AC AD AE~F XK
  </id>
  <id type='region' idStatus='special'>XA~B</id>
</idValidity></supplementalData>"""

SUPPLEMENTAL = """<?xml version="1.0" encoding="UTF-8" ?>
<supplementalData><codeMappings>
  <territoryCodes type="AC" alpha3="ASC"/>
  <territoryCodes type="AD" numeric="020" alpha3="AND" fips10="AN"/>
  <territoryCodes type="AE" numeric="784" alpha3="ARE" fips10="AE"/>
  <territoryCodes type="AF" numeric="004" alpha3="AFG" fips10="AF"/>
  <territoryCodes type="XK" numeric="983" alpha3="XKK"/>
  <territoryCodes type="XA" numeric="999" alpha3="XAA"/>
</codeMappings></supplementalData>"""

LANGUAGE = """<?xml version="1.0" encoding="UTF-8" ?>
<supplementalData><idValidity>
  <id type='language' idStatus='regular'><!-- 5 items -->
    aa~b ace de eng
  </id>
  <id type='language' idStatus='deprecated'>in iw tl</id>
</idValidity></supplementalData>"""

EN = """<?xml version="1.0" encoding="UTF-8" ?>
<ldml><localeDisplayNames>
 <languages>
  <language type="aa">Afar</language>
  <language type="ab">Abkhazian</language>
  <language type="de">German</language>
  <language type="de" alt="short">Deutsch</language>
  <language type="tl">Tagalog</language>
 </languages>
 <territories>
  <territory type="AD">Andorra</territory>
  <territory type="AE">United Arab Emirates</territory>
  <territory type="AE" alt="short">UAE</territory>
  <territory type="AF">Afghanistan</territory>
  <territory type="XK">Kosovo</territory>
 </territories>
</localeDisplayNames></ldml>"""


@pytest.fixture(scope="module")
def build():
    spec = importlib.util.spec_from_file_location("build_reference_packs", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_reference_packs"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def cldr_zip(tmp_path):
    path = tmp_path / "core.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("common/validity/region.xml", REGION)
        z.writestr("common/validity/language.xml", LANGUAGE)
        z.writestr("common/supplemental/supplementalData.xml", SUPPLEMENTAL)
        z.writestr("common/main/en.xml", EN)
    return path


def _rows(out: Path, dataset: str):
    from shape.refpacks.packs import Pack

    manifest = read_manifest(out)
    return Pack(out, manifest, "search path").table(dataset).to_pylist()


# --- us-zip-city ------------------------------------------------------------------------------


def test_us_zip_city_from_a_geonames_file(build, tmp_path):
    src = tmp_path / "US.txt"
    src.write_text(GEONAMES, encoding="utf-8")
    out = tmp_path / "out"
    build.build("us-zip-city", src, out, pin=False)
    assert _rows(out, "us_zip_city") == [
        {"zip": "00501", "city": "Holtsville", "state": "NY", "county": "Suffolk"},
        {"zip": "02872", "city": "Prudence Island", "state": "RI", "county": "Newport"},
        {"zip": "96799", "city": "Pago Pago", "state": "AS", "county": None},
    ]
    m = read_manifest(out)
    assert m["name"] == "us-zip-city" and m["license"] == "CC-BY-4.0"
    assert "GeoNames" in m["attribution"] and "CC-BY-4.0" in m["attribution"]
    assert hashlib.sha256(src.read_bytes()).hexdigest() in m["source"]
    assert m["datasets"][0]["fields"] == ["zip", "city", "state", "county"]


def test_us_zip_city_from_the_zip_archive_is_identical(build, tmp_path):
    src = tmp_path / "US.txt"
    src.write_text(GEONAMES, encoding="utf-8")
    archive = tmp_path / "US.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.write(src, "US.txt")
        z.writestr("readme.txt", "ignored")
    a, b = tmp_path / "a", tmp_path / "b"
    build.build("us-zip-city", src, a, pin=False)
    build.build("us-zip-city", archive, b, pin=False)
    assert read_manifest(a) == read_manifest(b)


def test_the_build_is_deterministic(build, tmp_path):
    src = tmp_path / "US.txt"
    src.write_text(GEONAMES, encoding="utf-8")
    build.build("us-zip-city", src, tmp_path / "a", pin=False)
    build.build("us-zip-city", src, tmp_path / "b", pin=False)
    assert read_manifest(tmp_path / "a") == read_manifest(tmp_path / "b")


def test_a_source_that_is_not_the_pinned_one_is_refused(build, tmp_path):
    src = tmp_path / "US.txt"
    src.write_text(GEONAMES, encoding="utf-8")
    with pytest.raises(SystemExit, match="pinned"):
        build.build("us-zip-city", src, tmp_path / "out")  # pin defaults on
    assert not (tmp_path / "out").exists()


def test_every_shipped_pack_has_a_pinned_source(build):
    for name in ("us-zip-city", "iso-3166-1", "iso-639-1", "iban-lengths"):
        assert len(build.PINNED[name]) == 64
    assert "iso-4217" not in build.PINNED and "iso-639" not in build.PINNED


def test_a_geonames_file_with_nothing_usable_is_an_error(build, tmp_path):
    src = tmp_path / "US.txt"
    src.write_text("US\t09720\tAPO AE\t\t\t\t\t\t\t0\t0\t\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="no usable"):
        build.build("us-zip-city", src, tmp_path / "out", pin=False)


def test_the_source_must_exist(build, tmp_path):
    with pytest.raises(SystemExit, match="not found"):
        build.build("us-zip-city", tmp_path / "missing.txt", tmp_path / "o", pin=False)


# --- check mode -------------------------------------------------------------------------------


def test_check_passes_on_a_fresh_build_and_fails_on_a_changed_file(build, tmp_path):
    src = tmp_path / "US.txt"
    src.write_text(GEONAMES, encoding="utf-8")
    out = tmp_path / "out"
    build.build("us-zip-city", src, out, pin=False)
    assert build.check("us-zip-city", src, out, pin=False) == []
    file = out / "us_zip_city.arrow"
    file.write_bytes(file.read_bytes()[:-9] + b"\0" * 9)
    problems = build.check("us-zip-city", src, out, pin=False)
    assert problems and "us_zip_city.arrow" in problems[0] and "checksum" in problems[0]


def test_check_fails_when_the_source_changed(build, tmp_path):
    src = tmp_path / "US.txt"
    src.write_text(GEONAMES, encoding="utf-8")
    out = tmp_path / "out"
    build.build("us-zip-city", src, out, pin=False)
    src.write_text(GEONAMES.replace("Holtsville\t", "Holtsville X\t", 1), encoding="utf-8")
    assert build.check("us-zip-city", src, out, pin=False)


def test_the_command_line_builds_and_checks(build, tmp_path, capsys):
    src = tmp_path / "US.txt"
    src.write_text(GEONAMES, encoding="utf-8")
    out = tmp_path / "out"
    base = ["us-zip-city", "--source", str(src), "--out", str(out), "--allow-unpinned-source"]
    assert build.main(base) == 0
    assert build.main([*base, "--check"]) == 0
    (out / "us_zip_city.arrow").write_bytes(b"x")
    assert build.main([*base, "--check"]) == 1
    assert "checksum" in capsys.readouterr().err


# --- the CLDR packs ---------------------------------------------------------------------------


def test_iso_3166_1_from_cldr(build, cldr_zip, tmp_path):
    out = tmp_path / "out"
    build.build("iso-3166-1", cldr_zip, out, pin=False)
    # AC has no numeric code, XA is not "regular", XK is user-assigned: all left out
    assert _rows(out, "iso_3166_1") == [
        {"alpha2": "AD", "alpha3": "AND", "numeric": "020", "name": "Andorra"},
        {"alpha2": "AE", "alpha3": "ARE", "numeric": "784", "name": "United Arab Emirates"},
        {"alpha2": "AF", "alpha3": "AFG", "numeric": "004", "name": "Afghanistan"},
    ]
    m = read_manifest(out)
    assert m["license"] == "Unicode-3.0" and "CLDR" in m["source"]
    assert "Unicode" in m["attribution"]


def test_iso_3166_1_needs_a_name_for_every_code(build, cldr_zip, tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(cldr_zip) as src, zipfile.ZipFile(bad, "w") as dst:
        for item in src.namelist():
            data = src.read(item)
            if item.endswith("en.xml"):
                data = data.replace(b"Andorra", b"").replace(
                    b'<territory type="AD"></territory>', b""
                )
            dst.writestr(item, data)
    with pytest.raises(SystemExit, match="AD"):
        build.build("iso-3166-1", bad, tmp_path / "out", pin=False)


def test_iso_639_1_two_letter_codes_only(build, cldr_zip, tmp_path):
    out = tmp_path / "out"
    build.build("iso-639-1", cldr_zip, out, pin=False)
    assert _rows(out, "iso_639_1") == [
        {"alpha2": "aa", "name": "Afar"},
        {"alpha2": "ab", "name": "Abkhazian"},
        {"alpha2": "de", "name": "German"},
        {"alpha2": "tl", "name": "Tagalog"},
    ]  # ace and eng: three letters; in, iw: deprecated; tl: deprecated by CLDR, kept by ISO
    m = read_manifest(out)
    assert m["name"] == "iso-639-1" and m["license"] == "Unicode-3.0"


def test_iso_639_1_needs_a_name_for_every_code(build, tmp_path):
    z = tmp_path / "core.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("common/validity/language.xml", LANGUAGE)
        f.writestr("common/main/en.xml", EN.replace('<language type="de">German</language>', ""))
    with pytest.raises(SystemExit, match="de"):
        build.build("iso-639-1", z, tmp_path / "out", pin=False)


def test_a_cldr_directory_works_but_is_never_pinned(build, tmp_path, cldr_zip):
    root = tmp_path / "cldr"
    with zipfile.ZipFile(cldr_zip) as z:
        z.extractall(root)
    build.build("iso-3166-1", root, tmp_path / "out", pin=False)
    assert len(_rows(tmp_path / "out", "iso_3166_1")) == 3
    with pytest.raises(SystemExit, match="pinned"):
        build.build("iso-3166-1", root, tmp_path / "out2")


def test_cldr_source_missing_a_member_is_an_error(build, tmp_path):
    z = tmp_path / "core.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("common/main/en.xml", EN)
    with pytest.raises(SystemExit, match="region.xml"):
        build.build("iso-3166-1", z, tmp_path / "out", pin=False)


# --- the IBAN length table --------------------------------------------------------------------


def test_iban_lengths_from_a_registry_file(build, tmp_path):
    src = tmp_path / "generated.json"
    src.write_text(
        json.dumps(
            {
                "DE": {"iban_length": 22, "country": "DE"},
                "AD": {"iban_length": 24, "country": "AD"},
                "XX": {"country": "XX"},
            }
        )
    )
    out = tmp_path / "out"
    with pytest.raises(SystemExit, match="XX"):
        build.build("iban-lengths", src, out, pin=False)
    src.write_text(
        json.dumps({"DE": {"iban_length": 22}, "AD": {"iban_length": 24}}), encoding="utf-8"
    )
    build.build("iban-lengths", src, out, pin=False)
    assert _rows(out, "iban_lengths") == [
        {"country": "AD", "length": 24},
        {"country": "DE", "length": 22},
    ]
    m = read_manifest(out)
    assert m["license"] == "MIT" and "schwifty" in m["source"]


# --- packs built locally (their data is not shipped) --------------------------------------------

SIX = """<?xml version="1.0" encoding="UTF-8"?>
<ISO_4217 Pblshd="2026-09-17"><CcyTbl>
<CcyNtry><CtryNm>A</CtryNm><CcyNm>Euro</CcyNm><Ccy>EUR</Ccy><CcyNbr>978</CcyNbr><CcyMnrUnts>2</CcyMnrUnts></CcyNtry>
<CcyNtry><CtryNm>B</CtryNm><CcyNm>Euro</CcyNm><Ccy>EUR</Ccy><CcyNbr>978</CcyNbr><CcyMnrUnts>2</CcyMnrUnts></CcyNtry>
<CcyNtry><CtryNm>C</CtryNm><CcyNm>Yen</CcyNm><Ccy>JPY</Ccy><CcyNbr>392</CcyNbr><CcyMnrUnts>0</CcyMnrUnts></CcyNtry>
<CcyNtry><CtryNm>D</CtryNm><CcyNm>Gold</CcyNm><Ccy>XAU</Ccy><CcyNbr>959</CcyNbr><CcyMnrUnts>N.A.</CcyMnrUnts></CcyNtry>
<CcyNtry><CtryNm>ANTARCTICA</CtryNm><CcyNm>No universal currency</CcyNm></CcyNtry>
</CcyTbl></ISO_4217>"""


def test_iso_4217_is_built_locally_from_a_list_file(build, tmp_path):
    src = tmp_path / "list-one.xml"
    src.write_text(SIX, encoding="utf-8")
    out = tmp_path / "out"
    build.build("iso-4217", src, out)  # no pin: the user's own copy
    assert _rows(out, "iso_4217") == [
        {"alphabetic_code": "EUR", "numeric_code": "978", "minor_units": 2},
        {"alphabetic_code": "JPY", "numeric_code": "392", "minor_units": 0},
        {"alphabetic_code": "XAU", "numeric_code": "959", "minor_units": None},
    ]
    m = read_manifest(out)
    assert m["name"] == "iso-4217"
    assert "not redistributed" in m["license"].lower() or "local" in m["license"].lower()


def test_iso_4217_with_contradicting_duplicates_is_an_error(build, tmp_path):
    src = tmp_path / "list-one.xml"
    src.write_text(
        SIX.replace("<CcyNbr>978</CcyNbr><CcyMnrUnts>2", "<CcyNbr>979</CcyNbr><CcyMnrUnts>2", 1)
    )
    with pytest.raises(SystemExit, match="EUR"):
        build.build("iso-4217", src, tmp_path / "out")


LOC = (
    "\ufeffaar||aa|Afar|afar\n"
    "alb|sqi|sq|Albanian|albanais\n"
    "ace|||Achinese|aceh\n"
    "cze|ces|cs|Czech|tchèque\n"
)


def test_iso_639_is_built_locally_from_a_library_of_congress_file(build, tmp_path):
    src = tmp_path / "ISO-639-2_utf-8.txt"
    src.write_text(LOC, encoding="utf-8")
    out = tmp_path / "out"
    build.build("iso-639", src, out)
    rows = _rows(out, "iso_639")
    assert rows == [
        {"alpha2": "aa", "alpha3_t": "aar", "name": "Afar"},
        {"alpha2": None, "alpha3_t": "ace", "name": "Achinese"},
        {"alpha2": "cs", "alpha3_t": "ces", "name": "Czech"},  # the T code, not the B code (cze)
        {"alpha2": "sq", "alpha3_t": "sqi", "name": "Albanian"},  # T (sqi), not B (alb)
    ]


def test_iso_639_rejects_a_malformed_line(build, tmp_path):
    src = tmp_path / "x.txt"
    src.write_text("aar|aa|Afar\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="line 1"):
        build.build("iso-639", src, tmp_path / "out")


def test_unknown_pack_name_is_refused(build, tmp_path):
    with pytest.raises(SystemExit, match="unknown pack"):
        build.build("nope", tmp_path, tmp_path / "o")
