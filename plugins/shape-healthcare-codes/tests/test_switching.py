"""One domain function works on any diagnosis system: the common code model."""

import datetime as dt
import subprocess
import sys
from pathlib import Path

import pytest
from shape_healthcare_codes import CodeSystem, load
from shape_healthcare_codes.byo import load_byo
from shape_healthcare_codes.model import DIAGNOSIS_SYSTEMS, CodeSet

# a hand-made file shaped like a licensed ICD-10-AM extract (the IHACPA site was not reachable
# from the build environment, so no real file was read; this is the loader's only test input)
AM = (
    "Code,Short Title,Long Title,Billable\n"
    "A00.0,Cholera classical,Cholera due to V. cholerae,N\n"
    "A00.01,Cholera x,Cholera invented,Y\n"
)
GM = (
    b'<?xml version="1.0"?><ClaML version="2.0.0">'
    b'<Class code="A00" kind="category"><SubClass code="A00.0"/>'
    b'<Rubric kind="preferred"><Label>Cholera</Label></Rubric></Class>'
    b'<Class code="A00.0" kind="category"><SuperClass code="A00"/>'
    b'<Rubric kind="preferred"><Label>Cholera, invented</Label></Rubric></Class></ClaML>'
)


def draw_reportable(cs: CodeSet, day: dt.date) -> list[str]:
    """What a domain does: the reportable codes of a system on a date of service."""
    return sorted(cs.codes_on(day, leaf_only=True).to_pylist())[:3]


def test_the_same_function_runs_on_us_and_international_systems(tmp_path: Path):
    am = tmp_path / "am.csv"
    am.write_text(AM, encoding="utf-8")
    gm = tmp_path / "gm.xml"
    gm.write_bytes(GM)
    load_byo("icd10am", am, data_dir=tmp_path / "d")
    load_byo("icd10gm", gm, data_dir=tmp_path / "d")
    day = dt.date(2026, 10, 2)
    us = draw_reportable(load(CodeSystem.ICD10CM), day)
    assert len(us) == 3
    assert draw_reportable(load(CodeSystem.ICD10AM, tmp_path / "d"), day) == ["A0001"]
    assert draw_reportable(load(CodeSystem.ICD10GM, tmp_path / "d"), day) == ["A000"]
    assert {CodeSystem.ICD10CM, CodeSystem.ICD10GM, CodeSystem.ICD10AM} <= set(DIAGNOSIS_SYSTEMS)


@pytest.mark.parametrize("module", ["shape_healthcare_codes", "shape_healthcare_codes.detectors"])
def test_import_is_light(module: str):
    ok = "'pyarrow' not in sys.modules" if module.endswith("codes") else "True"
    code = f"import sys, {module}; assert {ok}"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
