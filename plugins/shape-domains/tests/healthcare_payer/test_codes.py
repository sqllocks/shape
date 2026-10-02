"""Code sets: dates, billability, licensed-set handling and the NDC directory."""

from __future__ import annotations

from datetime import date

import pytest
from shape_domains.healthcare_payer import byo
from shape_domains.healthcare_payer.drugs import DRUGS, InterimNdcDirectory
from shape_domains.healthcare_payer.icd10cm import CONCEPTS, ICD10CM, resolve
from shape_domains.healthcare_payer.reference import (
    DRG,
    DRG_FAMILIES,
    HCC_OF,
    PCS,
    POS,
    pos_valid_on,
)
from shape_domains.healthcare_payer.services import SERVICES, SYSTEM_CPT, SYSTEM_SVC, code_for


def test_the_october_changes_are_honoured():
    assert ICD10CM["N18.3"].valid_on(date(2020, 9, 30)) and not ICD10CM["N18.3"].valid_on(
        date(2020, 10, 1)
    )
    assert not ICD10CM["N18.30"].valid_on(date(2020, 9, 30)) and ICD10CM["N18.30"].valid_on(
        date(2020, 10, 1)
    )
    assert (
        resolve("ckd3", date(2019, 5, 1)) == "N18.3"
        and resolve("ckd3", date(2022, 5, 1)) == "N18.30"
    )
    assert (
        resolve("low_back_pain", date(2021, 9, 30)) == "M54.5"
        and resolve("low_back_pain", date(2021, 10, 1)) == "M54.50"
    )
    assert (
        resolve("obesity_class1", date(2024, 9, 30)) == "E66.9"
        and resolve("obesity_class1", date(2024, 10, 1)) == "E66.811"
    )
    assert not ICD10CM["U07.1"].valid_on(date(2020, 3, 31)) and ICD10CM["U07.1"].valid_on(
        date(2020, 4, 1)
    )


def test_category_headers_are_not_billable_and_every_concept_resolves():
    assert not ICD10CM["E11"].billable and ICD10CM["E11.9"].billable
    for concept, options in CONCEPTS.items():
        assert all(o in ICD10CM for o in options), concept
        for day in (date(2016, 1, 1), date(2022, 1, 1), date(2025, 1, 1)):
            assert ICD10CM[resolve(concept, day)].valid_on(day), (concept, day)


def test_codes_have_well_formed_shape():
    for code, icd in ICD10CM.items():
        assert code == icd.code and 3 <= len(code) <= 8
        assert icd.age_min <= icd.age_max and icd.effective <= icd.end
        if "." in code:
            assert len(code.split(".")[0]) == 3
    for code in PCS:
        assert len(code) == 7 and code.isalnum() and code.upper() == code
    assert all(c in ICD10CM for c in HCC_OF)


def test_pos_telehealth_codes_start_in_2022():
    assert not pos_valid_on("10", date(2021, 12, 31)) and pos_valid_on("10", date(2022, 1, 1))
    assert pos_valid_on("02", date(2020, 6, 1)) and set(POS) >= {"11", "21", "23", "81"}


def test_drg_families_point_at_real_drgs_with_ordered_los_bounds():
    for fam in DRG_FAMILIES.values():
        assert all(code in DRG for code in fam)
    for d in DRG.values():
        assert d.los_min <= d.gmlos <= d.los_max and d.weight > 0


def test_cpt_is_never_shipped_by_default():
    assert all(s.hcpcs is None or s.hcpcs[0] in "ABCDEGHJKLMPQRSTV" for s in SERVICES.values())
    for s in SERVICES.values():
        system, code = code_for(s)
        assert system != SYSTEM_CPT
        if s.hcpcs is None:
            assert (system, code) == (SYSTEM_SVC, s.key)


def test_byo_cpt_table_replaces_the_service_key(tmp_path):
    path = tmp_path / "lic.csv"
    path.write_text(
        "table,key,value\ncpt,LAB_HBA1C,T0001\nrevenue,INPT_ROOM_BOARD,0120\ntob,inpatient:1,0111\n"
    )
    lic = byo.load_licensed(path)
    assert code_for(SERVICES["LAB_HBA1C"], lic.cpt) == (SYSTEM_CPT, "T0001")
    assert (
        code_for(SERVICES["G0463" if "G0463" in SERVICES else "HOSP_CLINIC_VISIT"], lic.cpt)[0]
        == "HCPCS"
    )
    assert lic.revenue["INPT_ROOM_BOARD"] == "0120" and lic.tob["inpatient:1"] == "0111"


@pytest.mark.parametrize(
    "row", ["cpt,NOT_A_KEY,T0001", "cpt,LAB_HBA1C,123", "revenue,X,12", "tob,x,ab1", "other,k,v"]
)
def test_byo_loader_rejects_bad_rows(tmp_path, row):
    path = tmp_path / "bad.csv"
    path.write_text(f"table,key,value\n{row}\n")
    with pytest.raises(ValueError):
        byo.load_licensed(path)


def test_ndc_directory_is_eleven_digit_unique_and_date_aware():
    ndc = InterimNdcDirectory()
    every = [r for k in DRUGS for r in ndc.packages(k, date(2024, 12, 31))]
    codes = [r.ndc for r in every]
    assert len(codes) == len(set(codes)) and all(len(c) == 11 and c.isdigit() for c in codes)
    assert all(r.interim for r in every)
    assert ndc.packages("tirzepatide", date(2022, 5, 31)) == []
    assert ndc.packages("tirzepatide", date(2022, 6, 1))
    assert not ndc.is_marketed(
        ndc.packages("tirzepatide", date(2022, 6, 1))[0].ndc, date(2022, 5, 1)
    )
    assert not ndc.is_marketed("00000000000", date(2024, 1, 1))
