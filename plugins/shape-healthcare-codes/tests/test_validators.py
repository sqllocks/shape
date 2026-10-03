import datetime as dt

from fixtures import MCE_AGE, MCE_SEX
from shape_healthcare_codes.builders import mce
from shape_healthcare_codes.validators import EditIndex, age_sex_violations

IDX = EditIndex.from_table(mce.parse(MCE_AGE, MCE_SEX))


def test_a_man_cannot_have_a_delivery_and_a_woman_cannot_have_prostate_cancer():
    assert age_sex_violations(IDX, "O80", 30, "M") == ["sex:F"]
    assert age_sex_violations(IDX, "C61", 70, "F") == ["sex:M"]
    assert age_sex_violations(IDX, "O80", 30, "F") == []
    assert age_sex_violations(IDX, "C61", 70, "male") == []


def test_age_categories_hold_at_their_boundaries():
    assert age_sex_violations(IDX, "O80", 8, "F") == ["age:maternity"]
    assert age_sex_violations(IDX, "O80", 9, "F") == []
    assert age_sex_violations(IDX, "O80", 64, "F") == []
    assert age_sex_violations(IDX, "O80", 65, "F") == ["age:maternity"]
    assert age_sex_violations(IDX, "N401", 14, "M") == ["age:adult"]
    assert age_sex_violations(IDX, "N401", 15, "M") == []
    assert age_sex_violations(IDX, "E8411", 17, None) == []
    assert age_sex_violations(IDX, "E8411", 18, None) == ["age:pediatric"]
    assert age_sex_violations(IDX, "Z00110", 0, None) == []
    assert age_sex_violations(IDX, "Z00110", 1, None) == ["age:perinatal"]


def test_both_edits_report_together_and_unknown_inputs_pass():
    assert sorted(age_sex_violations(IDX, "N401", 5, "F")) == ["age:adult", "sex:M"]
    assert age_sex_violations(IDX, "I10", 5, "F") == []  # on no list
    assert age_sex_violations(IDX, "O80", 30, "X") == []  # sex not binary: not checked
    assert age_sex_violations(IDX, "o.80", 30, "M") == ["sex:F"]  # dotted, any case


def test_procedure_sex_edits_use_their_own_system():
    assert age_sex_violations(IDX, "0UT90ZZ", 40, "M", system="icd10pcs") == ["sex:F"]
    assert age_sex_violations(IDX, "0UT90ZZ", 40, "M") == []  # not an ICD-10-CM code
    assert dt.date.today()  # keep the datetime import honest for future date-based edits
