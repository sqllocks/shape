import datetime as dt
from pathlib import Path

import pytest
from shape_healthcare_codes import store
from shape_healthcare_codes.byo import ByoError, load_byo, read_claml, read_delimited

NUCC = (
    "Code,Grouping,Classification,Specialization,Definition,Notes,Display Name,Section\n"
    "207Q00000X,Allopathic & Osteopathic Physicians,Family Medicine,,An invented definition,,"
    "Family Medicine Physician,Individual\n"
    "207R00000X,Allopathic & Osteopathic Physicians,Internal Medicine,,Another invented one,,"
    "Internal Medicine Physician,Individual\n"
)
CLAML = b"""<?xml version="1.0" encoding="UTF-8"?>
<ClaML version="2.0.0">
  <Class code="I" kind="chapter"><SubClass code="A00-A09"/>
    <Rubric kind="preferred"><Label>Infectious diseases</Label></Rubric></Class>
  <Class code="A00-A09" kind="block"><SuperClass code="I"/><SubClass code="A00"/>
    <Rubric kind="preferred"><Label>Intestinal</Label></Rubric></Class>
  <Class code="A00" kind="category"><SuperClass code="A00-A09"/><SubClass code="A00.0"/>
    <Rubric kind="preferred"><Label>Cholera</Label></Rubric></Class>
  <Class code="A00.0" kind="category"><SuperClass code="A00"/>
    <Rubric kind="preferred"><Label>Cholera due to <Term>Vibrio</Term> cholerae</Label>
    </Rubric></Class>
</ClaML>"""


def test_nucc_style_csv_maps_to_the_common_model():
    t = read_delimited(NUCC)
    rows = {r["code"]: r for r in t.to_pylist()}
    assert set(rows) == {"207Q00000X", "207R00000X"}
    r = rows["207Q00000X"]
    assert r["short_desc"] == "Family Medicine Physician"
    assert r["long_desc"] == "An invented definition"
    assert r["leaf"] is True and r["valid_from"] is None
    assert r["grouping"] == "Allopathic & Osteopathic Physicians" and r["section"] == "Individual"


def test_explicit_column_mapping_dates_leaf_and_delimiters():
    text = (
        "CPT|Descriptor|Billable|Effective\n10021|Invented procedure one|Y|2020-01-01\n10022|X|N|\n"
    )
    t = read_delimited(text, {"code": "CPT", "long_desc": "Descriptor"})
    rows = {r["code"]: r for r in t.to_pylist()}
    assert rows["10021"]["leaf"] is True and rows["10022"]["leaf"] is False
    assert (
        rows["10021"]["valid_from"] == dt.date(2020, 1, 1) and rows["10022"]["valid_from"] is None
    )
    assert rows["10021"]["long_desc"] == "Invented procedure one"


def test_duplicate_codes_blank_codes_and_bom():
    t = read_delimited("﻿code,description\nA1,first\nA1,second\n,blank\n")
    assert t.column("code").to_pylist() == ["A1"] and t.column("long_desc").to_pylist() == ["first"]


def test_unreadable_input_is_an_error_not_an_empty_set():
    with pytest.raises(ByoError, match="no data rows"):
        read_delimited("code,description\n")
    with pytest.raises(ByoError, match="code column"):
        read_delimited("foo,bar\n1,2\n")
    with pytest.raises(ByoError, match="ClaML"):
        read_claml(b"<root><x/></root>")


def test_claml_leaf_is_a_category_without_subclasses():
    t = read_claml(CLAML)
    rows = {r["code"]: r for r in t.to_pylist()}
    assert set(rows) == {"A00", "A000"}  # the chapter and the block are not codes
    assert rows["A00"]["leaf"] is False and rows["A000"]["leaf"] is True
    assert rows["A000"]["long_desc"] == "Cholera due to Vibrio cholerae"


def test_load_byo_writes_the_asset_with_provenance(tmp_path: Path):
    src = tmp_path / "nucc.csv"
    src.write_text(NUCC, encoding="utf-8")
    out = load_byo("nucc_taxonomy", src, data_dir=tmp_path / "data")
    assert out.name == "nucc_taxonomy.arrow"
    cs = store.load("nucc_taxonomy", tmp_path / "data")
    assert "207Q00000X" in cs and len(cs) == 2
    meta = store.manifest("nucc_taxonomy", tmp_path / "data")
    assert meta["byo"] is True and meta["redistributable"] is False
    assert len(meta["sources"]["nucc.csv"]) == 64


def test_claml_is_detected_and_a_free_system_is_refused(tmp_path: Path):
    src = tmp_path / "icd10gm.xml"
    src.write_bytes(CLAML)
    load_byo("icd10gm", src, data_dir=tmp_path / "d")
    assert "A000" in store.load("icd10gm", tmp_path / "d")
    with pytest.raises(ByoError, match="not a bring-your-own system"):
        load_byo("icd10cm", src, data_dir=tmp_path / "d")
    with pytest.raises(ByoError, match="unknown format"):
        load_byo("cpt", src, fmt="pdf", data_dir=tmp_path / "d")


def test_missing_asset_says_how_to_get_it(tmp_path: Path):
    with pytest.raises(store.AssetMissing, match="fetch icd10cm"):
        store.load("icd10cm", tmp_path)
    with pytest.raises(store.AssetMissing):
        store.manifest("icd10cm", tmp_path)


def test_data_dir_environment_and_available(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SHAPE_HEALTHCARE_CODES_DIR", str(tmp_path / "mine"))
    assert store.user_dir() == tmp_path / "mine"
    src = tmp_path / "c.csv"
    src.write_text("code,description\nX1,x\n", encoding="utf-8")
    load_byo("cpt", src)  # default data dir: the environment's
    assert store.available()["cpt"] == "user"
    assert store.available()["icd10cm"] == "shipped"
    monkeypatch.delenv("SHAPE_HEALTHCARE_CODES_DIR")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert store.user_dir() == tmp_path / "xdg" / "shape" / "healthcare-codes"


def test_a_user_built_asset_overrides_the_shipped_one(tmp_path: Path):
    t = store.read_table("icd10cm").slice(0, 3)
    store.write_asset("icd10cm", t, {"release": "mine"}, tmp_path)
    assert len(store.load("icd10cm", tmp_path)) == 3
    assert len(store.load("icd10cm")) > 1000  # no data_dir: user dir, then shipped


def test_xml_that_declares_entities_is_refused_not_expanded():
    from shape_healthcare_codes._xml import UnsafeXml, parse_xml

    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><x>&b;</x>'
    )
    with pytest.raises(UnsafeXml):
        parse_xml(bomb)
    with pytest.raises(UnsafeXml):
        read_claml(bomb)
    doctype = b'<?xml version="1.0"?><!DOCTYPE ClaML SYSTEM "ClaML.dtd"><a/>'
    assert parse_xml(doctype).tag == "a"  # an external DTD reference is not fetched or expanded
