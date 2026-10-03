"""Every other builder, against hand-made inputs in the real layout."""

import datetime as dt
from pathlib import Path

import pytest
from fixtures import (
    CCSR_CSV,
    HCC_CSV,
    HCPCS_CPT_LINE,
    HCPCS_TEXT,
    MCE_AGE,
    MCE_SEX,
    NDC_PACKAGE,
    NDC_PRODUCT,
    PCS_R1,
    PCS_R2,
    POS_HTML,
    RXN_CONSO,
    RXN_SAT,
    order_text,
    zip_bytes,
)
from shape_healthcare_codes import store
from shape_healthcare_codes.builders import ccsr, hcc, hcpcs, icd10pcs, mce, ndc, pos, rxnorm
from shape_healthcare_codes.builders._releases import parse_order
from shape_healthcare_codes.model import Release
from shape_healthcare_codes.validators import NdcIndex, ndc_marketed

D = dt.date


def test_pcs_release_validity():
    t = icd10pcs.build_table(
        [
            (Release("FY2021", D(2020, 10, 1)), parse_order(order_text(PCS_R1))),
            (Release("2021-12", D(2020, 12, 1)), parse_order(order_text(PCS_R2))),
        ]
    )
    cs = store.CodeSet("icd10pcs", t)
    assert cs.is_valid("0FT44ZZ", D(2020, 10, 1), leaf_only=True)
    assert not cs.is_valid("0FT47ZZ", D(2020, 11, 30))
    assert cs.is_valid("0FT47ZZ", D(2020, 12, 1), leaf_only=True)
    assert cs.get("0FT47ZZ").attrs["section"] == "0"  # type: ignore[union-attr]
    assert not cs.is_valid("0FT44ZZ", D(2020, 9, 30))  # before the first release built


def test_pcs_pins_cover_every_release():
    assert set(icd10pcs.PINS) == {r.id for r in icd10pcs.RELEASES}


def test_hcpcs_codes_modifiers_continuations_and_dates():
    t = hcpcs.parse(HCPCS_TEXT)
    rows = {(r["kind"], r["code"]): r for r in t.to_pylist()}
    assert set(rows) == {("code", "J9999"), ("code", "A4000"), ("modifier", "ZZ")}
    j = rows[("code", "J9999")]
    assert j["long_desc"] == "Injection, invented drug alfa, 1 mg"
    assert j["betos"] == "O1E" and j["pricing_indicator"] == "51" and j["coverage_code"] == "C"
    assert j["valid_from"] == D(2010, 1, 1) and j["valid_to"] is None
    a = rows[("code", "A4000")]
    assert a["long_desc"].endswith("first line of the record and continues here")
    assert a["valid_to"] == D(2024, 1, 1)
    assert rows[("modifier", "ZZ")]["long_desc"] == (
        "Invented modifier, level two second line of the modifier"
    )


def test_hcpcs_refuses_a_cpt_record_instead_of_storing_it():
    with pytest.raises(hcpcs.CptRecordError):
        hcpcs.parse(HCPCS_TEXT + "\n" + HCPCS_CPT_LINE)


def test_hcpcs_rejects_a_damaged_line():
    with pytest.raises(ValueError, match="record layout"):
        hcpcs.parse("J9999001003Injection")


def test_ndc_normalizes_every_layout_and_takes_dates_from_the_package():
    t = ndc.parse(NDC_PRODUCT, NDC_PACKAGE)
    got = {r["code"]: r for r in t.to_pylist()}
    assert set(got) == {"00002015201", "00002015261", "01234567890", "12345067890"}
    assert got["00002015201"]["proprietary_name"] == "Inventa"
    assert got["00002015261"]["valid_from"] == D(2025, 5, 1)  # package date, not product date
    assert got["00002015261"]["sample_package"] is True
    assert got["01234567890"]["dea_schedule"] == "CII"
    assert got["01234567890"]["valid_to"] == D(2023, 12, 31)
    assert got["12345067890"]["short_desc"] == "GammaCare"
    assert got["01234567890"]["short_desc"] == "fakeprilate"  # no brand: the generic name


def test_ndc_marketed_on_the_fill_date():
    idx = NdcIndex.from_table(ndc.parse(NDC_PRODUCT, NDC_PACKAGE))
    assert ndc_marketed(idx, "0002-0152-01", D(2024, 3, 28))
    assert not ndc_marketed(idx, "0002-0152-01", D(2024, 3, 27))  # before the start
    assert ndc_marketed(idx, "1234-5678-90", D(2023, 12, 31))
    assert not ndc_marketed(idx, "1234-5678-90", D(2024, 1, 1))  # after the end
    assert ndc_marketed(idx, "12345-678-90", D(2030, 1, 1))  # no end date: still marketed
    assert not ndc_marketed(idx, "99999-999-99", D(2024, 6, 1))  # not in the directory
    assert not ndc_marketed(idx, "1234567890", D(2024, 6, 1))  # ambiguous 10 digits
    assert not ndc_marketed(idx, "garbage", D(2024, 6, 1))
    assert "00002015201" in idx and "0002-0152-01" in idx and "x" not in idx and 5 not in idx


def test_rxnorm_keeps_prescribable_term_types_and_ndcs():
    t = rxnorm.parse_conso(RXN_CONSO.splitlines(keepends=True))
    rows = {r["code"]: r for r in t.to_pylist()}
    assert set(rows) == {"617310", "83367", "153165"}  # not SY, not MTHSPL, not obsolete
    assert rows["617310"]["leaf"] is True and rows["83367"]["leaf"] is False
    assert rows["617310"]["tty"] == "SCD"
    n = rxnorm.parse_ndc(RXN_SAT.splitlines(keepends=True))
    assert n.to_pylist() == [{"rxcui": "617310", "ndc11": "00071015523"}]


def test_rxnorm_build_reads_both_files_from_the_zip(tmp_path: Path):
    z = tmp_path / "rx.zip"
    z.write_bytes(zip_bytes({"rrf/RXNCONSO.RRF": RXN_CONSO, "rrf/RXNSAT.RRF": RXN_SAT}))
    out, manifest = rxnorm.build(from_files={"zip": z})
    assert set(out) == {"rxnorm", "rxnorm_ndc"} and out["rxnorm_ndc"].num_rows == 1
    assert "publicly available data courtesy of the U.S. National Library of Medicine" in str(
        manifest["attribution"]
    )


def test_pos_table():
    t = pos.parse(POS_HTML)
    got = {r["code"]: r for r in t.to_pylist()}
    assert set(got) == {"01", "02", "11"}  # the 35-40 range is not a code
    assert got["01"]["valid_from"] == D(2003, 10, 1)  # the earliest "Effective" date
    assert got["02"]["short_desc"] == "Telehealth Provided Other than in Patient’s Home"
    assert got["11"]["valid_from"] is None
    with pytest.raises(ValueError, match="layout changed"):
        pos.parse("<html>nothing</html>")


def test_hcc_long_table_with_models_and_payment_years():
    rows = hcc.parse(HCC_CSV).to_pylist()
    assert {"code": "E1165", "model": "ESRD V24", "hcc": "38", "payment_years": []} in rows
    assert {"code": "E1165", "model": "CMS-HCC V28", "hcc": "37", "payment_years": [2026]} in rows
    assert {"code": "E1165", "model": "RxHCC V08", "hcc": "30", "payment_years": []} in rows
    assert {"code": "I10", "model": "RxHCC V08", "hcc": "187", "payment_years": [2026]} in rows
    assert not [r for r in rows if r["code"] == "I10" and r["model"] != "RxHCC V08"]
    assert len(rows) == 3 + 1 + 2  # empty category cells give no row
    with pytest.raises(ValueError, match="header"):
        hcc.parse("a,b\n1,2\n")


def test_ccsr_long_table_and_defaults():
    rows = ccsr.parse(CCSR_CSV).to_pylist()
    assert rows == [
        {
            "code": "A009",
            "ccsr": "DIG001",
            "ccsr_desc": "Intestinal infection",
            "default_ip": True,
            "default_op": True,
        },
        {
            "code": "A009",
            "ccsr": "INF003",
            "ccsr_desc": "Bacterial infections",
            "default_ip": False,
            "default_op": False,
        },
    ]
    with pytest.raises(ValueError, match="header"):
        ccsr.parse("a,b\n")


def test_mce_age_and_sex_lists_exclude_the_table_of_contents_and_other_sections():
    t = mce.parse(MCE_AGE, MCE_SEX).to_pylist()
    age = {(r["code"], r["value"]): (r["age_min"], r["age_max"]) for r in t if r["edit"] == "age"}
    assert age == {
        ("A33", "perinatal"): (0, 0),
        ("Z00110", "perinatal"): (0, 0),
        ("E8411", "pediatric"): (0, 17),
        ("O80", "maternity"): (9, 64),
        ("N401", "adult"): (15, 124),
    }
    sex = {(r["system"], r["code"]): r["value"] for r in t if r["edit"] == "sex"}
    assert sex == {
        ("icd10cm", "O80"): "F",
        ("icd10cm", "N736"): "F",
        ("icd10pcs", "0UT90ZZ"): "F",
        ("icd10cm", "N401"): "M",
        ("icd10cm", "C61"): "M",
        ("icd10pcs", "0VT08ZZ"): "M",
    }
    assert "D630" not in {r["code"] for r in t}
    with pytest.raises(ValueError, match="sections"):
        mce.parse(MCE_AGE, "nothing")
