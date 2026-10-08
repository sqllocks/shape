"""W8-02: risk-adjustment hierarchies and coefficients, and the reference score.

Every builder test reads the committed excerpts of the published CMS files (``tests/data``);
nothing here uses the network.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pytest
from fixtures import DATA, mappings_zip, software_zip
from shape_healthcare_codes import store
from shape_healthcare_codes.builders import BUILDERS, _cms_software, hcc_coefficients, run
from shape_healthcare_codes.builders import hcc_hierarchy as hier
from shape_healthcare_codes.byo import ByoError, load_byo
from shape_healthcare_codes.model import CodeSystem
from shape_healthcare_codes.risk import apply_hierarchy, mapping_index, score, table_problems

V28 = "CMS-HCC V28"
RX = "RxHCC V08"


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """``hcc``, ``hcc_hierarchy`` and ``hcc_coefficients`` built from the excerpts."""
    d = tmp_path_factory.mktemp("risk")
    sw = software_zip(d)
    run("hcc", d / "data", from_files={"zip": mappings_zip(d)})
    run("hcc_hierarchy", d / "data", from_files={"zip": sw})
    run("hcc_coefficients", d / "data", from_files={"zip": sw})
    return d / "data"


def _packages(tmp_path: Path, edit=None) -> list[_cms_software.Package]:  # type: ignore[no-untyped-def]
    return _cms_software.read_software_zip(software_zip(tmp_path, edit))


# --- 1. hierarchy table -------------------------------------------------------------------


def test_hierarchy_table_from_the_published_macros(built: Path):
    t = store.read_table("hcc_hierarchy", built)
    assert t.schema.field("drops").type == pa.list_(pa.string())
    rows = t.to_pylist()
    assert {r["model"] for r in rows} == {V28, RX, "ESRD V24"}
    v28 = [(r["hcc"], r["drops"]) for r in rows if r["model"] == V28]
    assert v28[0] == ("17", ["18", "19", "20", "21", "22", "23"])  # the published order
    # a %STR( list that the published file breaks over two lines
    assert ("221", ["222", "223", "224", "225", "226", "227"]) in v28
    assert ("222", ["223", "224", "225", "226", "227"]) in v28
    assert ("63", ["64", "65", "68", "202"]) in v28
    # two RxHCC packages carry the same hierarchy: kept once
    rx = [(r["hcc"], r["drops"]) for r in rows if r["model"] == RX]
    assert rx == [("30", ["31"]), ("183", ["184", "186", "187"]), ("184", ["186", "187"])] + [
        ("186", ["187"])
    ]
    meta = store.manifest("hcc_hierarchy", built)
    assert meta["format"] == "shape-hcc-hierarchy" and meta["version"] == 1
    assert meta["release"].startswith("2027 Initial Model Software")
    assert meta["sources"]["2027-initial-model-software.zip"]


def test_hierarchy_layout_changes_raise(tmp_path: Path):
    def no_macro(pkg: str, files: dict[str, str | bytes]) -> dict[str, str | bytes]:
        return {k: v for k, v in files.items() if not k.endswith("H1.TXT")}

    with pytest.raises(ValueError, match="hierarchy macro"):
        hier.from_packages(_packages(tmp_path, no_macro))
    with pytest.raises(ValueError, match="no %SET0"):
        hier.parse(" %MACRO V28115H1;\n %MEND V28115H1;\n", V28)
    with pytest.raises(ValueError, match="not category numbers"):
        hier.parse("%SET0(CC=17 , HIER=%STR(18, HCC19 ));", V28)
    with pytest.raises(ValueError, match="not category numbers"):
        hier.parse("%SET0(CC=17 , HIER=%STR( ));", V28)
    with pytest.raises(ValueError, match="model packages"):
        _cms_software.read_packages(_zip({"readme.txt": b"no packages"}))

    def disagree(pkg: str, files: dict[str, str | bytes]) -> dict[str, str | bytes]:
        if pkg.endswith(".Y1"):
            text = bytes(files["R08X84H1.TXT"]).replace(b"HIER=%STR(31 )", b"HIER=%STR(32 )")
            files = {**files, "R08X84H1.TXT": text}
        return files

    with pytest.raises(ValueError, match="different hierarchies"):
        hier.from_packages(_packages(tmp_path, disagree))


# --- 2. coefficient table -----------------------------------------------------------------


def test_coefficient_table_keeps_the_published_text(built: Path):
    t = store.read_table("hcc_coefficients", built)
    assert t.schema.field("coefficient").type == pa.string()  # no float rounding
    rows = t.to_pylist()
    by = {(r["software"], r["segment"], r["variable"]): r for r in rows}
    r = by[("V2826.115.T2", "COMMUNITY_NA", "HCC17")]
    assert r["model"] == V28 and r["coefficient"] == "4.209" and r["payment_years"] == [2027]
    assert by[("V2826.115.T2", "COMMUNITY_NA", "D1")]["coefficient"] == "0.000"
    assert by[("V2826.115.T2", "INSTITUTIONAL", "HF_CHR_LUNG_V28")]["coefficient"] == "0.145"
    assert by[("V2826.115.T2", "SNP_NEW_ENROLLEE", "NMCAID_NORIGDIS_NEF70_74")]["coefficient"] == (
        "1.195"
    )
    # RxHCC: the leading blank of " 0.168" is not part of the number; two packages, one model
    assert by[("R0826.84.T2", "CE_NoLowAged", "M65_69")]["coefficient"] == "0.168"
    assert by[("R0826.84.Y1", "CE_NoLowAged", "M65_69")]["coefficient"] == "0.144"
    assert by[("R0826.84.T2", "CE_LTI", "RXHCC30")]["model"] == RX
    # ESRD: the segment is the published score variable (leading "_SCORE_" removed), the longest
    # prefix wins (GNPN_ over GNE_...), and a factor without a segment keeps its whole name
    esrd = {
        (r["segment"], r["variable"]): r["coefficient"] for r in rows if r["model"] == "ESRD V24"
    }
    assert esrd[("G_COMM_ND_PBD_LT65", "HCC85")] == "0.273"
    assert esrd[("G_COMM_ND_PBD_GE65", "HCC85")] == "0.251"
    assert esrd[("GRAFT_NE", "FBD_ORIGDIS_G_NEF65")] == "1.585"
    assert esrd[("DIAL", "F70_74")] == "0.624"
    assert esrd[(None, "TRANSPLANT_KIDNEY_ONLY_1M")] == "5.985"
    assert esrd[(None, "ActAdj_DUR4_9")] == "0.905" and esrd[(None, "LTI_GE65")] == "0.955"
    # every row of the excerpt is in the table, in the published order
    v28 = [r["variable"] for r in rows if r["software"] == "V2826.115.T2"]
    published = DATA / "cms_2027_model_software/CMS-HCC software V2826.115.T2/C2824T2N.csv"
    names = [ln.split(",")[0] for ln in published.read_text("latin-1").splitlines()[1:]]
    assert len(v28) == len(names) and names[4] == "CNA_HCC17" and v28[4] == "HCC17"
    meta = store.manifest("hcc_coefficients", built)
    assert meta["format"] == "shape-hcc-coefficients" and meta["version"] == 1


def test_segments_are_read_from_the_main_macro():
    text = (
        "%&SCOREMAC(PVAR=SCORE_COMMUNITY_NA,  RLIST=&COMM_REGA, CPREF=CNA_);\n"
        "%&SCOREMAC(PVAR=_SCORE_GRAFT_NE, RLIST=&MOD_GRAFT_NE,\n      CPREF=GNE_);\n"
        "%MACRO SCOREVAR( PVAR=, RLIST=, CPREF=);\n"  # the macro's own definition is not a call
    )
    assert hcc_coefficients.parse_segments(text) == [("COMMUNITY_NA", "CNA_"), ("GRAFT_NE", "GNE_")]


def test_coefficient_layout_changes_raise(tmp_path: Path):
    def edit(change):  # type: ignore[no-untyped-def]
        def f(pkg: str, files: dict[str, str | bytes]) -> dict[str, str | bytes]:
            return change(dict(files)) if pkg.startswith("CMS-HCC") else files

        return f

    def header(files):  # type: ignore[no-untyped-def]
        files["C2824T2N.csv"] = bytes(files["C2824T2N.csv"]).replace(b"Coeff", b"Value", 1)
        return files

    def not_a_number(files):  # type: ignore[no-untyped-def]
        files["C2824T2N.csv"] = bytes(files["C2824T2N.csv"]).replace(b"4.209", b"4.2O9")
        return files

    def no_segments(files):  # type: ignore[no-untyped-def]
        del files["V2826T1M.TXT"]
        return files

    def no_year(files):  # type: ignore[no-untyped-def]
        del files["V2826T2P.TXT"]
        return files

    def two_csvs(files):  # type: ignore[no-untyped-def]
        files["extra.csv"] = files["C2824T2N.csv"]
        return files

    def empty(files):  # type: ignore[no-untyped-def]
        files["C2824T2N.csv"] = b"Name, Coeff, Label\r\n"
        return files

    for change, message in (
        (header, "header"),
        (not_a_number, "not a decimal"),
        (no_segments, "SCOREMAC"),
        (no_year, "DATE_ASOF"),
        (two_csvs, "one coefficient .csv"),
        (empty, "no rows"),
    ):
        with pytest.raises(ValueError, match=message):
            hcc_coefficients.from_packages(_packages(tmp_path, edit(change)))


# --- 3. loaders, catalog, CLI, bring-your-own ----------------------------------------------


def _run(argv: list[str]) -> int:
    """The plugin command, as ``shape healthcare-codes ARGV`` runs it (a local helper: another
    ``test_cli`` module may be on the path when the plugin and core suites run together)."""
    import argparse

    from shape_healthcare_codes.cli import HealthcareCodesCommand

    cmd = HealthcareCodesCommand()
    p = argparse.ArgumentParser()
    cmd.configure(p)
    return cmd.run(p.parse_args(argv))


def test_tables_are_catalogued_fetchable_and_listed(built: Path):
    from shape_healthcare_codes.provenance import all_assets

    for asset in ("hcc_hierarchy", "hcc_coefficients"):
        assert asset in BUILDERS and CodeSystem(asset).value == asset
        a = all_assets()[asset]
        assert a.mode == "fetch" and a.checked_on == "2026-10-04" and "2027" in a.release
        assert a.download_urls == (_cms_software.URL,)
    assert {"hcc_hierarchy", "hcc_coefficients"} <= set(store.available(built))


def test_cli_lists_fetches_and_loads_byo(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    assert _run(["list", "--json"]) == 0
    rows = {r["asset"]: r for r in json.loads(capsys.readouterr().out)}
    assert rows["hcc_hierarchy"]["fetchable"] and rows["hcc_coefficients"]["mode"] == "fetch"
    sw = software_zip(tmp_path)
    d = tmp_path / "d"
    assert _run(["fetch", "hcc_hierarchy", "--dir", str(d), "--file", f"zip={sw}"]) == 0
    assert store.read_table("hcc_hierarchy", d).num_rows == 22 + 4 + 4  # V28, ESRD, RxHCC
    assert _run(["byo", "hcc_coefficients", str(sw), "--format", "zip", "--dir", str(d)]) == 0
    assert store.manifest("hcc_coefficients", d)["byo"] is True
    assert _run(["notices", "hcc_coefficients"]) == 0
    assert "2027 Initial Model Software" in capsys.readouterr().out


def test_byo_reads_the_published_zip_or_a_delimited_file(tmp_path: Path):
    sw = software_zip(tmp_path)
    out = load_byo("hcc_hierarchy", sw, data_dir=tmp_path / "a")
    assert out.name == "hcc_hierarchy.arrow"
    meta = store.manifest("hcc_hierarchy", tmp_path / "a")
    assert meta["format"] == "shape-hcc-hierarchy" and meta["version"] == 1
    assert meta["byo"] is True and len(meta["sources"][sw.name]) == 64
    fetched = hier.from_packages(_cms_software.read_software_zip(sw))
    assert store.read_table("hcc_hierarchy", tmp_path / "a").equals(fetched)

    h = tmp_path / "h.csv"
    h.write_text('model,hcc,drops\nCMS-HCC V28,17,"18, 19"\nCMS-HCC V28,18,19 23\n', "utf-8")
    load_byo("hcc_hierarchy", h, data_dir=tmp_path / "b")
    assert store.read_table("hcc_hierarchy", tmp_path / "b").to_pylist() == [
        {"model": V28, "hcc": "17", "drops": ["18", "19"]},
        {"model": V28, "hcc": "18", "drops": ["19", "23"]},
    ]
    c = tmp_path / "c.tsv"
    c.write_text(
        "model\tsegment\tvariable\tcoefficient\tpayment_years\n"
        "CMS-HCC V28\tCOMMUNITY_NA\tHCC17\t4.209\t2026;2027\n"
        "ESRD V24\t\tLTI_GE65\t0.955\t\n",
        "utf-8",
    )
    load_byo("hcc_coefficients", c, data_dir=tmp_path / "b")
    assert store.read_table("hcc_coefficients", tmp_path / "b").to_pylist() == [
        {
            "model": V28,
            "segment": "COMMUNITY_NA",
            "variable": "HCC17",
            "coefficient": "4.209",
            "payment_years": [2026, 2027],
            "software": "user supplied",
        },
        {
            "model": "ESRD V24",
            "segment": None,
            "variable": "LTI_GE65",
            "coefficient": "0.955",
            "payment_years": [],
            "software": "user supplied",
        },
    ]


@pytest.mark.parametrize(
    ("asset", "text", "message"),
    [
        ("hcc_hierarchy", "model,hcc\nCMS-HCC V28,17\n", "missing columns"),
        ("hcc_hierarchy", "model,hcc,drops\nCMS-HCC V28,17,\n", "category numbers"),
        ("hcc_hierarchy", "model,hcc,drops\nCMS-HCC V28,HCC17,18\n", "category numbers"),
        ("hcc_hierarchy", "model,hcc,drops\n,17,18\n", "no model"),
        ("hcc_hierarchy", "model,hcc,drops\n", "no data rows"),
        ("hcc_coefficients", "model,segment,variable,coefficient\nM,S,V,1e-3\n", "decimal"),
        ("hcc_coefficients", "model,segment,variable,coefficient\nM,S,,0.1\n", "no variable"),
        (
            "hcc_coefficients",
            "model,segment,variable,coefficient,payment_years\nM,S,V,0.1,PY27\n",
            "not years",
        ),
    ],
)
def test_byo_refuses_a_bad_table_file(tmp_path: Path, asset: str, text: str, message: str):
    f = tmp_path / "t.csv"
    f.write_text(text, "utf-8")
    with pytest.raises(ByoError, match=message):
        load_byo(asset, f, data_dir=tmp_path)
    with pytest.raises(ByoError, match="unknown format"):
        load_byo(asset, f, fmt="claml", data_dir=tmp_path)
    bad_zip = tmp_path / "x.zip"
    bad_zip.write_bytes(_zip({"readme.txt": b""}))
    with pytest.raises(ByoError, match="model packages"):
        load_byo(asset, bad_zip, data_dir=tmp_path)
    assert not (tmp_path / f"{asset}.arrow").exists()


# --- persisted format: declaration, compatibility, newer versions ---------------------------

COMPAT = DATA / "compat_v1"


def test_version_1_files_written_by_this_release_still_load():
    """Compatibility: files of format version 1, committed when the format was introduced."""
    h = store.read_table("hcc_hierarchy", COMPAT)
    assert h.to_pylist() == [{"model": V28, "hcc": "17", "drops": ["18", "19", "20"]}]
    c = store.read_table("hcc_coefficients", COMPAT)
    assert c.to_pylist() == [
        {
            "model": V28,
            "segment": "COMMUNITY_NA",
            "variable": "HCC17",
            "coefficient": "4.209",
            "payment_years": [2027],
            "software": "V2826.115.T2",
        }
    ]
    for asset in ("hcc_hierarchy", "hcc_coefficients"):
        meta = store.manifest(asset, COMPAT)
        assert meta["version"] == 1 and meta["format"] == store.TABLE_FORMATS[asset][0]


def _write(tmp_path: Path, asset: str, schema_meta: dict[bytes, bytes], manifest: dict) -> None:  # type: ignore[type-arg]
    t = store.read_table(asset, COMPAT)
    store.write_asset(asset, t.replace_schema_metadata(schema_meta), manifest, tmp_path)


@pytest.mark.parametrize("asset", ["hcc_hierarchy", "hcc_coefficients"])
def test_a_newer_or_foreign_declaration_is_refused(tmp_path: Path, asset: str):
    fmt = store.TABLE_FORMATS[asset][0]
    good = {b"format": fmt.encode(), b"version": b"1"}
    _write(tmp_path, asset, good, {"format": fmt, "version": 2})
    with pytest.raises(
        store.UnsupportedVersion, match="version 2: this release reads up to version 1;"
    ):
        store.read_table(asset, tmp_path)
    _write(tmp_path, asset, {b"format": fmt.encode(), b"version": b"3"}, {})
    with pytest.raises(store.UnsupportedVersion, match="version 3"):
        store.read_table(asset, tmp_path)
    _write(tmp_path, asset, {b"format": b"shape-model", b"version": b"1"}, {})
    with pytest.raises(store.UnsupportedVersion, match="format is 'shape-model'"):
        store.read_table(asset, tmp_path)
    for bad in (0, "1.5", True, 1.0, None):
        _write(tmp_path, asset, good, {"format": fmt, "version": bad})
        with pytest.raises(store.UnsupportedVersion, match="not an integer of at least 1"):
            store.read_table(asset, tmp_path)
    # no declaration at all: the implicit version 1
    _write(tmp_path, asset, {}, {})
    assert store.read_table(asset, tmp_path).num_rows == 1
    # the check is only for these tables: another asset's manifest is not read for it
    store.write_asset("pos", pa.table({"code": ["01"]}), {"version": 99}, tmp_path)
    assert store.read_table("pos", tmp_path).num_rows == 1


# --- 4. reference scoring -----------------------------------------------------------------
# Worked examples, computed by hand from the excerpts of the published 2027 files:
#   mapping:      "2027 Initial ICD-10-CM Mappings.csv" (V28 and V08 columns)
#   hierarchy:    V28115H1.TXT and R08X84H1.TXT
#   coefficients: C2824T2N.csv (CMS-HCC V28), R0827T11.csv (RxHCC T2), R0827Y61.csv (RxHCC Y1)


def test_example_1_community_non_dual_aged_with_an_interaction(built: Path):
    # Female 70-74. E11.65 -> HCC38, I50.9 -> HCC226; no hierarchy between them; 2 payment HCCs.
    #   CNA_F70_74 0.395 + CNA_HCC38 0.166 + CNA_HCC226 0.360 + CNA_DIABETES_HF_V28 0.112
    #   + CNA_D2 0.000 = 1.033
    got = score(
        ["E11.65", "I50.9"],
        V28,
        "COMMUNITY_NA",
        {"F70_74": 1, "DIABETES_HF_V28": 1},
        payment_year=2027,
        data_dir=built,
    )
    assert got["categories"] == ["38", "226"] and got["dropped"] == []
    assert got["terms"] == [
        {"variable": "HCC38", "coefficient": Decimal("0.166")},
        {"variable": "HCC226", "coefficient": Decimal("0.360")},
        {"variable": "D2", "coefficient": Decimal("0.000")},
        {"variable": "F70_74", "coefficient": Decimal("0.395")},
        {"variable": "DIABETES_HF_V28", "coefficient": Decimal("0.112")},
    ]
    assert got["score"] == Decimal("1.033")


def test_example_2_a_more_severe_cancer_drops_the_less_severe_ones(built: Path):
    # Male 80-84. C79.51 -> HCC18, C34.90 -> HCC20, C61 -> HCC23. "Neoplasm 2": 18 drops 19-23.
    #   CNA_M80_84 0.571 + CNA_HCC18 2.341 + CNA_D1 0.000 = 2.912
    got = score(["C7951", "C34.90", "C61"], V28, "COMMUNITY_NA", ["M80_84"], data_dir=built)
    assert got["categories"] == ["18"] and got["dropped"] == ["20", "23"]
    assert got["score"] == Decimal("2.912")
    # the person with only the less severe ones keeps them; "Neoplasm 4": 20 drops 21-23
    #   CNA_M80_84 0.571 + CNA_HCC20 1.136 + CNA_D1 0.000 = 1.707
    got = score(["C34.90", "C61"], V28, "COMMUNITY_NA", ["M80_84"], data_dir=built)
    assert got["categories"] == ["20"] and got["dropped"] == ["23"]
    assert got["score"] == Decimal("1.707")
    got = score(["C61"], V28, "COMMUNITY_NA", ["M80_84"], data_dir=built)
    assert got["categories"] == ["23"] and got["dropped"] == []
    assert got["score"] == Decimal("0.571") + Decimal("0.186")


def test_example_3_institutional_with_heart_failure_hierarchy(built: Path):
    # Female 75-79, institutional. J44.9 -> HCC280, I50.23 -> HCC224, I50.9 -> HCC226, I10 has no
    # V28 category. "Heart 4": 224 drops 225-227, so HCC226 is dropped; 2 payment HCCs left.
    #   INS_F75_79 0.965 + INS_HCC224 0.217 + INS_HCC280 0.312 + INS_HF_CHR_LUNG_V28 0.145
    #   + INS_D2 0.000 = 1.639
    got = score(
        ["J449", "I5023", "I509", "I10"],
        V28,
        "INSTITUTIONAL",
        {"F75_79": True, "HF_CHR_LUNG_V28": 1, "LTIMCAID": 0},
        data_dir=built,
    )
    assert got["categories"] == ["224", "280"] and got["dropped"] == ["226"]
    assert got["score"] == Decimal("1.639")


def test_example_4_rxhcc_needs_the_package_when_several_publish_the_segment(built: Path):
    # Male 65-69. E11.65 -> RXHCC30, E11.9 -> RXHCC31, I10 -> RXHCC187. "Diabetes 1": 30 drops 31.
    #   T2 (PDP+MAPD): Rx_CE_NoLowAged_M65_69 0.168 + RXHCC30 0.499 + RXHCC187 0.057 = 0.724
    #   Y1 (MAPD only): 0.144 + 0.429 + 0.045 = 0.618
    codes = ["E1165", "E119", "I10"]
    with pytest.raises(ValueError, match=r"software= one of: R0826\.84\.T2, R0826\.84\.Y1"):
        score(codes, RX, "CE_NoLowAged", ["M65_69"], data_dir=built)
    t2 = score(codes, RX, "CE_NoLowAged", ["M65_69"], software="R0826.84.T2", data_dir=built)
    assert t2["categories"] == ["30", "187"] and t2["dropped"] == ["31"]
    assert t2["score"] == Decimal("0.724")
    y1 = score(codes, RX, "CE_NoLowAged", ["M65_69"], software="R0826.84.Y1", data_dir=built)
    assert y1["score"] == Decimal("0.618")
    with pytest.raises(ValueError, match="unknown software"):
        score(codes, RX, "CE_NoLowAged", software="R0826.84.Y9", data_dir=built)


def test_example_5_hierarchies_apply_in_the_published_order(built: Path):
    # Female 70-74. Z94.4 -> HCC62, K72.90 -> HCC63, G93.1 -> HCC202. "Liver 1": 62 drops 63;
    # "Liver 2" (63 drops 202) then no longer fires, as in the CMS macro, so HCC202 stays.
    #   CNA_F70_74 0.395 + CNA_HCC62 0.376 + CNA_HCC202 0.543 + CNA_D2 0.000 = 1.314
    got = score(["Z944", "K7290", "G931"], V28, "COMMUNITY_NA", ["F70_74"], data_dir=built)
    assert got["categories"] == ["62", "202"] and got["dropped"] == ["63"]
    assert got["score"] == Decimal("1.314")
    # without HCC62, HCC63 fires and drops HCC202
    got = score(["K7290", "G931"], V28, "COMMUNITY_NA", ["F70_74"], data_dir=built)
    assert got["categories"] == ["63"] and got["dropped"] == ["202"]


def test_new_enrollee_segment_and_codes_without_a_category(built: Path):
    # NE_NMCAID_NORIGDIS_NEF70_74 0.694: the new-enrollee segment has no category coefficients
    got = score(["E1165"], V28, "NEW_ENROLLEE", ["NMCAID_NORIGDIS_NEF70_74"], data_dir=built)
    assert got["categories"] == ["38"]
    assert got["terms"] == [
        {"variable": "NMCAID_NORIGDIS_NEF70_74", "coefficient": Decimal("0.694")}
    ]
    assert got["score"] == Decimal("0.694")
    nothing = score(["I10", "R69", "not a code", ""], V28, "COMMUNITY_NA", data_dir=built)
    assert nothing == {"categories": [], "dropped": [], "terms": [], "score": Decimal(0)}
    assert score([], V28, "COMMUNITY_NA", ["F70_74", "F70_74"], data_dir=built)["score"] == (
        Decimal("0.395")  # a variable named twice counts once
    )


def test_unknown_values_name_the_allowed_ones(built: Path):
    with pytest.raises(ValueError, match=r"unknown model 'CMS-HCC V24'; allowed: CMS-HCC V28"):
        score([], "CMS-HCC V24", "COMMUNITY_NA", data_dir=built)
    with pytest.raises(
        ValueError, match=r"unknown segment .* allowed: COMMUNITY_NA, COMMUNITY_ND, INSTITUTIONAL"
    ):
        score([], V28, "community non-dual aged", data_dir=built)
    with pytest.raises(ValueError, match=r"unknown variable .*'F99'.*allowed: .*F70_74"):
        score([], V28, "COMMUNITY_NA", ["F99"], data_dir=built)
    with pytest.raises(ValueError, match="derived from the codes"):
        score([], V28, "COMMUNITY_NA", ["HCC17"], data_dir=built)
    with pytest.raises(ValueError, match="derived from the codes"):
        score([], V28, "COMMUNITY_NA", {"D1": 1}, data_dir=built)
    with pytest.raises(ValueError, match="0 or 1"):
        score([], V28, "COMMUNITY_NA", {"F70_74": 2}, data_dir=built)
    with pytest.raises(ValueError, match="mapping of variable"):
        score([], V28, "COMMUNITY_NA", "F70_74", data_dir=built)
    with pytest.raises(ValueError, match=r"unknown payment_year .* 2026; allowed: 2027"):
        score([], V28, "COMMUNITY_NA", payment_year=2026, data_dir=built)
    with pytest.raises(store.AssetMissing):
        score([], V28, "COMMUNITY_NA", data_dir=built.parent / "empty")


def test_count_variables_at_their_boundaries(tmp_path: Path, built: Path):
    d = tmp_path / "d"
    for asset in ("hcc", "hcc_hierarchy"):
        store.write_asset(asset, store.read_table(asset, built), {}, d)
    f = tmp_path / "c.csv"
    rows = ["HCC17", "HCC37", "HCC63", "HCC202", "HCC226", "HCC280", "D1", "D2", "D3P"]
    f.write_text(
        "model,segment,variable,coefficient,payment_years\n"
        + "".join(f"CMS-HCC V28,S,{v},0.{i + 1}00,2027\n" for i, v in enumerate(rows)),
        "utf-8",
    )
    load_byo("hcc_coefficients", f, data_dir=d)

    def counts(codes: list[str]) -> list[str]:
        terms = score(codes, V28, "S", data_dir=d)["terms"]
        return [t["variable"] for t in terms if t["variable"].startswith("D")]

    assert counts([]) == []  # no category: no count variable
    assert counts(["C7801"]) == []  # C78.01 is not in the mapping excerpt
    assert counts(["C771"]) == ["D1"]
    assert counts(["C771", "E0821"]) == ["D2"]
    assert counts(["C771", "E0821", "K7290"]) == ["D3P"]  # "n or more" from n
    assert counts(["C771", "E0821", "K7290", "I0981", "B4481"]) == ["D3P"]
    # a category without a coefficient in the segment is not counted (HCC18 has none here)
    assert counts(["C771", "C770"]) == ["D1"]  # and 17 drops 18 anyway
    assert counts(["C770", "E0821"]) == ["D1"]


def test_mapping_payment_year_flags_select_rows_only_when_present():
    t = pa.table(
        {
            "code": ["A1", "A2", "B1"],
            "model": ["M", "M", "N"],
            "hcc": ["1", "2", "3"],
            "payment_years": [[2026], [], []],
        }
    )
    assert mapping_index(t, "M", None) == {"A1": {"1"}, "A2": {"2"}}
    assert mapping_index(t, "M", 2026) == {"A1": {"1"}}  # flags for 2026 exist: A2 is "No"
    assert mapping_index(t, "M", 2027) == {"A1": {"1"}, "A2": {"2"}}  # no 2027 flags at all
    assert mapping_index(t, "N", 2026) == {"B1": {"3"}}


def test_apply_hierarchy_alone():
    rules = [("17", ["18", "19"]), ("18", ["19"])]
    assert apply_hierarchy(["19", "18"], rules) == (["18"], ["19"])
    assert apply_hierarchy(["19"], rules) == (["19"], [])
    assert apply_hierarchy([], rules) == ([], [])
    assert apply_hierarchy(["17", "18", "19"], rules) == (["17"], ["18", "19"])


def test_public_name_is_the_reference_score():
    import shape_healthcare_codes as hc
    from shape_healthcare_codes import risk

    assert hc.risk_score is score and "risk_score" in hc.__all__
    assert "**not** a certified implementation" in (risk.__doc__ or "")


# --- 5. tables join the mapping -----------------------------------------------------------


def test_every_hierarchy_and_coefficient_category_is_in_the_mapping(built: Path):
    m = store.read_table("hcc", built)
    h = store.read_table("hcc_hierarchy", built)
    c = store.read_table("hcc_coefficients", built)
    assert table_problems(m, h, c) == []
    # and the check finds a category the mapping lacks, for that model only
    keep = [not (r["model"] == V28 and r["hcc"] == "17") for r in m.to_pylist()]
    assert table_problems(m.filter(pa.array(keep)), h, c) == [
        "hcc_hierarchy: CMS-HCC V28 category 17 is not in the mapping",
        "hcc_coefficients: CMS-HCC V28 variable HCC17 is not in the mapping",
    ]
    other = m.to_pylist()[0] | {"model": "CMS-HCC V22"}
    assert table_problems(pa.Table.from_pylist([other]), h.slice(0, 0), c.slice(0, 0)) == []


def _zip(files: dict[str, bytes]) -> bytes:
    from fixtures import zip_bytes

    return zip_bytes(dict(files))
