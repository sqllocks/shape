# ruff: noqa: E501
"""Every rate the simulator uses, with its public source.

``RATES`` is the single calibration table: a model never hard-codes a probability, it reads
``Calibration.get(name)``.  ``Calibration`` can be overridden from a captured shape (a mapping of
name to value) so a customer's own utilisation replaces the public figure, and
``render_markdown`` writes the table that ships in ``docs/domains/healthcare_payer.md`` (a test
fails when the two drift).

``status`` is honest about what we have verified: ``cited`` = the figure is a rounded value from
the publication named in ``source`` as we know it, not re-fetched when this was built (every cited
row therefore carries ``[VERIFY]`` for the reviewer); ``assumption`` = a modelling choice with no
direct published figure, set so an aggregate that *is* cited comes out right (the aggregate is
named in ``note`` and checked by the measured report).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

BANDS = ("0-17", "18-34", "35-44", "45-54", "55-64", "65-74", "75+")
_EDGES = (18, 35, 45, 55, 65, 75)


def band(age: int) -> str:
    for edge, name in zip(_EDGES, BANDS, strict=False):
        if age < edge:
            return name
    return BANDS[-1]


def _by_band(*values: float) -> dict[str, float]:
    assert len(values) == len(BANDS)
    return dict(zip(BANDS, values, strict=True))


@dataclass(frozen=True, slots=True)
class Rate:
    name: str
    value: Any
    unit: str
    source: str
    status: str  # "cited" or "assumption"
    note: str = ""


def _r(
    name: str, value: Any, unit: str, source: str, status: str = "cited", note: str = ""
) -> Rate:
    return Rate(name, value, unit, source, status, note)


_LOB = ("commercial", "ma", "medicaid")
_NCHS = "CDC/NCHS"

_RATES: tuple[Rate, ...] = (
    # ---- population -------------------------------------------------------------------------
    _r(
        "pop.lob_mix",
        {"commercial": 0.55, "ma": 0.20, "medicaid": 0.25},
        "share of members",
        "KFF Health Insurance Coverage of the Total Population (employer 54%, Medicaid/CHIP 20%, Medicare 18%)",
        "assumption",
        "configuration default for a mixed payer; set per run",
    ),
    _r(
        "pop.female_share",
        {"commercial": 0.51, "ma": 0.56, "medicaid": 0.56},
        "share female",
        "US Census ACS 2022 health insurance by sex; CMS Medicare Enrollment Dashboard; MACPAC enrollment",
    ),
    _r(
        "pop.age_commercial_subscriber",
        _by_band(0, 0.22, 0.26, 0.27, 0.25, 0, 0),
        "share of subscribers",
        "US Census ACS 2022 employed population by age; BLS Current Population Survey",
        "assumption",
        "subscribers are working-age adults; 65+ commercial members are not generated",
    ),
    _r(
        "pop.age_medicaid_adult",
        _by_band(0, 0.38, 0.22, 0.18, 0.14, 0.05, 0.03),
        "share of adult members",
        "MACPAC Medicaid and CHIP enrollment by age; KFF Medicaid enrollment by age",
        "assumption",
    ),
    _r(
        "pop.medicaid_child_share",
        0.43,
        "share of Medicaid members under 18",
        "MACPAC MACStats (children are about 40-45% of enrollees)",
    ),
    _r(
        "pop.ma_age",
        {"65-69": 0.28, "70-74": 0.27, "75-79": 0.20, "80-84": 0.13, "85+": 0.12},
        "share of MA members 65+",
        "CMS Medicare Enrollment Dashboard / KFF Medicare Advantage Enrollment Update",
    ),
    _r(
        "pop.ma_disabled_share",
        0.10,
        "share of MA members under 65 (disability entitlement)",
        "KFF Medicare Advantage Enrollment Update (about 10% of beneficiaries are under 65)",
    ),
    _r(
        "pop.spouse_prob",
        0.55,
        "share of commercial subscribers with a spouse on the plan",
        "KFF Employer Health Benefits Survey (family coverage about 45% of covered workers); US Census ACS",
        "assumption",
    ),
    _r(
        "pop.child_count",
        {"0": 0.45, "1": 0.22, "2": 0.22, "3": 0.11},
        "children per subscriber with dependents",
        "US Census Families and Living Arrangements (average 1.9 children in families with children)",
        "assumption",
    ),
    # ---- eligibility ------------------------------------------------------------------------
    _r(
        "elig.annual_term",
        {"commercial": 0.16, "ma": 0.06, "medicaid": 0.28},
        "annual disenrolment probability",
        "KFF/Commonwealth Fund coverage churn studies; MACPAC Medicaid churn; CMS MA disenrolment (about 5-10% a year)",
        "assumption",
    ),
    _r(
        "elig.reenroll_prob",
        {"commercial": 0.35, "ma": 0.10, "medicaid": 0.55},
        "share of terminated members who return",
        "MACPAC Medicaid churn (about 1/3 to 1/2 return within a year)",
        "assumption",
    ),
    _r(
        "elig.gap_days",
        {"commercial": (30, 180), "ma": (30, 120), "medicaid": (30, 240)},
        "gap length range, days",
        "MACPAC / Urban Institute churn studies",
        "assumption",
    ),
    _r(
        "elig.cob_prob",
        {"commercial": 0.045, "ma": 0.02, "medicaid": 0.06},
        "share of members with other coverage",
        "CMS COB&R / NAIC; Census ACS multiple coverage (about 4-5% commercial)",
        "assumption",
    ),
    _r(
        "elig.dual_share_medicaid_65",
        0.06,
        "Medicaid members that are also Medicare (65+)",
        "MACPAC Dually eligible beneficiaries data book",
        "assumption",
    ),
    # ---- chronic conditions: diagnosed prevalence by age band -------------------------------
    _r(
        "cond.obesity",
        _by_band(0.19, 0.36, 0.42, 0.45, 0.44, 0.41, 0.35),
        "prevalence",
        f"{_NCHS} NHANES 2017-March 2020 (children 19.7%, adults 41.9%)",
    ),
    _r(
        "cond.obesity_coded",
        0.45,
        "share of obese members with an obesity code on a claim",
        "published claims studies (obesity under-coded; 30-50%)",
        "assumption",
    ),
    _r(
        "cond.htn",
        _by_band(0.01, 0.06, 0.15, 0.30, 0.46, 0.60, 0.72),
        "diagnosed prevalence",
        f"{_NCHS} NHANES 2017-2020 hypertension prevalence by age (22% 18-39, 55% 40-59, 75% 60+), diagnosed share about 70%",
    ),
    _r(
        "cond.dm",
        _by_band(0.003, 0.02, 0.05, 0.10, 0.15, 0.21, 0.24),
        "diagnosed diabetes prevalence",
        "CDC National Diabetes Statistics Report 2023 (diagnosed 8.9% of adults; 3% 18-44, 12% 45-64, 17-21% 65+)",
    ),
    _r(
        "cond.dm1_share",
        {"child": 0.95, "adult": 0.07},
        "share of diabetes that is type 1",
        "CDC National Diabetes Statistics Report (T1D about 5-10% of adult diabetes; most youth diabetes)",
    ),
    _r(
        "cond.lipid",
        _by_band(0.01, 0.05, 0.12, 0.25, 0.38, 0.50, 0.48),
        "diagnosed lipid disorder prevalence",
        f"{_NCHS} NHANES 2017-2020 (high total cholesterol 11.5%; claims prevalence of dyslipidemia 30%+ over 45)",
        "assumption",
    ),
    _r(
        "cond.ckd",
        _by_band(0.0, 0.004, 0.006, 0.02, 0.04, 0.10, 0.18),
        "diagnosed CKD prevalence",
        "CDC CKD Surveillance System 2023 (14% of adults; about 90% of stage 1-3 unaware; 36% of 65+ by labs)",
        "assumption",
    ),
    _r(
        "cond.asthma",
        _by_band(0.065, 0.08, 0.077, 0.077, 0.075, 0.07, 0.065),
        "current asthma prevalence",
        f"{_NCHS} NHIS 2021 (adults 7.7%, children 6.5%)",
    ),
    _r(
        "cond.copd",
        _by_band(0.0, 0.0, 0.0, 0.025, 0.06, 0.10, 0.12),
        "prevalence",
        "CDC BRFSS COPD prevalence (6.4% of adults, about 11-12% over 65)",
    ),
    _r(
        "cond.depression",
        _by_band(0.06, 0.09, 0.09, 0.09, 0.08, 0.06, 0.06),
        "coded prevalence",
        "SAMHSA NSDUH 2021 major depressive episode (8.3% of adults, 20% of adolescents) times a coded share about 0.5-1",
        "assumption",
    ),
    _r(
        "cond.anxiety",
        _by_band(0.03, 0.09, 0.08, 0.07, 0.06, 0.05, 0.05),
        "coded prevalence",
        "NIMH anxiety disorders (19% of adults in a year) and claims-coded prevalence (about 7-9%)",
        "assumption",
    ),
    _r(
        "cond.adhd",
        _by_band(0.07, 0.04, 0.03, 0.02, 0.01, 0.0, 0.0),
        "diagnosed prevalence (age >= 3)",
        f"{_NCHS} / CDC ADHD (children 3-17: 9.8%; adults 4.4%)",
    ),
    _r(
        "cond.sud_opioid",
        _by_band(0.0, 0.007, 0.007, 0.006, 0.004, 0.002, 0.001),
        "coded prevalence",
        "SAMHSA NSDUH 2021 opioid use disorder (0.7% of adults 12+)",
    ),
    _r(
        "cond.sud_alcohol",
        _by_band(0.0, 0.012, 0.014, 0.014, 0.012, 0.006, 0.003),
        "coded prevalence",
        "SAMHSA NSDUH 2021 alcohol use disorder (10.6% of 12+; moderate-severe and treated is a small share)",
        "assumption",
    ),
    _r(
        "cond.bipolar",
        0.007,
        "coded prevalence, age >= 15",
        "NIMH bipolar disorder (2.8% of adults, 4.4% lifetime); coded 0.5-1%",
        "assumption",
    ),
    _r(
        "cond.schizophrenia",
        0.004,
        "coded prevalence, age >= 15",
        "NIMH schizophrenia (about 0.25-0.64%)",
    ),
    _r(
        "cond.hypothyroid",
        {"F": 0.08, "M": 0.02},
        "adult prevalence by sex",
        "American Thyroid Association general information (hypothyroidism about 5% of US adults, women 5-8x men)",
    ),
    _r(
        "cond.gerd",
        0.12,
        "adult diagnosed prevalence",
        "ACG GERD guideline (about 20% symptomatic weekly; diagnosed about 10-15%)",
        "assumption",
    ),
    _r(
        "cond.autoimmune",
        0.010,
        "diagnosed prevalence of rheumatoid arthritis, psoriasis and inflammatory bowel disease treated by a specialist, age >= 18",
        "Arthritis Foundation / CDC (RA 0.6-1%), NPF (psoriasis 3%, a third treated), CCFA (IBD 0.7%); about 0.7-1.2% of commercial members use a specialty biologic (IQVIA)",
        "assumption",
    ),
    _r(
        "cond.afib",
        _by_band(0.0, 0.002, 0.004, 0.01, 0.03, 0.08, 0.12),
        "prevalence",
        "CDC atrial fibrillation fact sheet (2.7-6.1 million; 9% at 65+)",
    ),
    _r(
        "cond.hf",
        _by_band(0.0, 0.001, 0.003, 0.01, 0.025, 0.06, 0.12),
        "prevalence",
        "AHA Heart Disease and Stroke Statistics 2023 (heart failure 2.4% of adults; 6.7 million)",
    ),
    _r(
        "cond.cad",
        _by_band(0.0, 0.002, 0.01, 0.04, 0.08, 0.14, 0.20),
        "prevalence",
        "AHA Heart Disease and Stroke Statistics 2023 (CAD 7.1% of adults; 5% to 25% by age)",
        "assumption",
    ),
    _r(
        "cond.osteoporosis_f",
        _by_band(0, 0, 0, 0.02, 0.07, 0.15, 0.25),
        "female prevalence",
        "NIH/NHANES 2017-2018 osteoporosis (19.6% of women 50+); claims-coded about half",
        "assumption",
    ),
    _r(
        "cond.bph_m",
        _by_band(0, 0, 0.01, 0.06, 0.18, 0.30, 0.38),
        "male diagnosed BPH",
        "NIDDK BPH statistics (50% of men 51-60, 90% 80+; diagnosed share lower)",
        "assumption",
    ),
    # ---- comorbidity clustering (odds ratios applied on top of the age prevalence) ----------
    _r(
        "cluster.obesity_or",
        {"htn": 2.2, "dm": 2.8, "lipid": 1.8, "ckd": 1.3, "asthma": 1.4, "depression": 1.4},
        "odds ratio vs non-obese",
        "CDC NHANES / published meta-analyses (obesity: HTN OR 2-3, T2D OR 3-7, dyslipidaemia OR 1.5-2.5)",
        "assumption",
    ),
    _r(
        "cluster.dm_or",
        {"htn": 2.5, "lipid": 3.6, "ckd": 3.0, "cad": 2.0, "hf": 2.0, "depression": 1.6},
        "odds ratio vs non-diabetic",
        "ADA Standards of Care 2023 (HTN in about 70-75% of adults with diabetes; dyslipidaemia in about 70-80%)",
        "assumption",
    ),
    _r(
        "cluster.htn_or",
        {"ckd": 2.2, "cad": 2.0, "hf": 2.5, "afib": 1.8},
        "odds ratio vs normotensive",
        "AHA 2017 ACC/AHA hypertension guideline; USRDS 2022",
        "assumption",
    ),
    _r(
        "target.dm_with_htn",
        (0.60, 0.85),
        "P(HTN | diabetes), acceptable band",
        "ADA Standards of Care 2023: about 70% of adults with diabetes have hypertension",
    ),
    _r(
        "target.dm_with_lipid",
        (0.55, 0.85),
        "P(lipid | diabetes), band",
        "ADA Standards of Care 2023: dyslipidaemia in 70-80% of adults with diabetes",
    ),
    _r(
        "target.dm_with_obesity",
        (0.45, 0.80),
        "P(obesity | diabetes), band",
        f"{_NCHS} NHANES: 87% of adults with diabetes are overweight or obese, about 55-60% obese",
    ),
    _r(
        "target.dm_with_ckd",
        (0.05, 0.30),
        "P(CKD diagnosis | diabetes), band",
        "CDC CKD Surveillance 2023 (diabetes in 38% of CKD; about 15% of diabetics diagnosed)",
    ),
    # ---- diabetes care ----------------------------------------------------------------------
    _r(
        "dm.type2_on_insulin",
        0.25,
        "share of type 2 on insulin",
        "CDC National Diabetes Statistics Report 2023 (insulin use among adults with diabetes about 31%)",
    ),
    _r(
        "dm.metformin_share",
        0.62,
        "share of type 2 on metformin",
        "published claims studies (metformin first line, 55-70% of treated T2D)",
    ),
    _r(
        "dm.newer_agent_share",
        0.28,
        "share of type 2 on SGLT2/GLP-1/DPP-4",
        "published claims studies 2019-2022 (about 25-35%)",
        "assumption",
    ),
    _r(
        "dm.hba1c_per_year",
        2.6,
        "HbA1c tests per diabetic member-year",
        "ADA Standards of Care (2-4 a year); HEDIS HBD testing rate about 90%",
    ),
    _r(
        "dm.eye_exam_annual",
        0.58,
        "annual dilated eye exam",
        "NCQA HEDIS EED (commercial about 55-60%, MA about 65%)",
    ),
    _r(
        "dm.foot_exam_annual",
        0.35,
        "annual foot exam or podiatry visit",
        "NHIS diabetes care (about 70% self-report; claims-visible about 30-40%)",
        "assumption",
    ),
    _r(
        "dm.uacr_annual",
        0.45,
        "annual urine albumin-creatinine ratio",
        "NCQA HEDIS KED (about 40-45% 2022)",
    ),
    _r(
        "dm.statin_age_40_75",
        0.62,
        "statin use in diabetics aged 40-75",
        "ADA / CDC (about 60% of diabetics over 40 on statins)",
    ),
    _r(
        "dm.complication_hazard",
        0.035,
        "annual probability of a new complication code",
        "UKPDS / CDC (microvascular complications 2-4% a year in T2D)",
        "assumption",
    ),
    _r(
        "dm.visits_per_year",
        3.4,
        "PCP/endocrine visits per diabetic member-year",
        "NAMCS 2019 (diabetes visits about 3-4 a year)",
        "assumption",
    ),
    # ---- other chronic care -----------------------------------------------------------------
    _r(
        "htn.visits_per_year",
        2.2,
        "visits per hypertensive member-year",
        "NAMCS 2019 (hypertension as a visit reason 2-3 a year)",
        "assumption",
    ),
    _r(
        "htn.treated",
        0.78,
        "share of diagnosed hypertensives on a drug",
        "CDC NHANES 2017-2020 (about 75-80% of diagnosed on treatment)",
    ),
    _r(
        "lipid.statin_treated",
        0.55,
        "share of lipid-disorder members on a statin",
        "CDC NCHS Data Brief 2020 (28% of adults 40+ on statins; about half of those with high LDL)",
        "assumption",
    ),
    _r(
        "lipid.panel_per_year",
        0.75,
        "lipid panel per lipid-disorder member-year",
        "ACC/AHA cholesterol guideline (annual monitoring)",
        "assumption",
    ),
    _r(
        "ckd.progress_hazard",
        {"1": 0.03, "2": 0.03, "3": 0.06, "4": 0.15, "5": 0.20},
        "annual stage progression",
        "USRDS 2022 / KDIGO (eGFR decline 1-4 mL/min/yr)",
        "assumption",
    ),
    _r(
        "ckd.nephrology_visits",
        2.0,
        "nephrology visits a year, stage >= 3",
        "KDIGO 2012 monitoring frequency",
        "assumption",
    ),
    _r(
        "asthma.exac_rate",
        0.40,
        "exacerbations per asthmatic member-year",
        "CDC NHIS 2021 (about 40% of adults with asthma had an attack in a year)",
    ),
    _r(
        "asthma.exac_ed_share",
        0.18,
        "exacerbations that go to the ED",
        "CDC National Asthma Data (asthma ED visits 4-5 per 100 asthmatics)",
        "assumption",
    ),
    _r(
        "copd.exac_rate",
        0.85,
        "exacerbations per COPD member-year",
        "Hurst et al NEJM 2010 ECLIPSE (mean 0.85-1.3 a year)",
    ),
    _r(
        "copd.exac_admit_share",
        0.20,
        "exacerbations that need admission",
        "HCUP / Medicare (about 20% of COPD exacerbations)",
        "assumption",
    ),
    _r(
        "resp.season_weight",
        {
            1: 1.7,
            2: 1.6,
            3: 1.2,
            4: 0.9,
            5: 0.7,
            6: 0.6,
            7: 0.6,
            8: 0.7,
            9: 0.9,
            10: 1.0,
            11: 1.3,
            12: 1.7,
        },
        "relative monthly weight",
        "CDC FluView ILI surveillance, NAMCS respiratory visit seasonality (winter peak 2-3x summer trough)",
        "assumption",
    ),
    _r(
        "resp.acute_per_year",
        {
            "0-17": 1.3,
            "18-34": 0.40,
            "35-44": 0.38,
            "45-54": 0.40,
            "55-64": 0.42,
            "65-74": 0.45,
            "75+": 0.50,
        },
        "acute respiratory encounters per member-year",
        "NAMCS 2019 (acute upper respiratory infections about 2% of visits)",
        "assumption",
    ),
    _r(
        "resp.flu_share",
        0.12,
        "share of respiratory episodes coded as influenza",
        "CDC burden estimates 2019-20 (8% of population symptomatic flu)",
        "assumption",
    ),
    _r(
        "resp.covid_wave",
        {
            "2022-01": 0.040,
            "2022-02": 0.020,
            "2022-07": 0.012,
            "2022-12": 0.008,
            "2023-01": 0.008,
            "2023-08": 0.006,
            "2024-01": 0.006,
            "2024-08": 0.005,
        },
        "monthly COVID-19 coded encounter probability per member",
        "CDC COVID Data Tracker case and visit trends 2022-2024",
        "assumption",
    ),
    _r(
        "flu_vaccine.rate",
        {
            "0-17": 0.50,
            "18-34": 0.30,
            "35-44": 0.35,
            "45-54": 0.40,
            "55-64": 0.48,
            "65-74": 0.68,
            "75+": 0.72,
        },
        "annual flu vaccination",
        "CDC FluVaxView 2022-23 coverage by age",
    ),
    # ---- behavioural health -----------------------------------------------------------------
    _r(
        "bh.visits_per_year",
        {
            "depression": 6.0,
            "anxiety": 5.0,
            "adhd": 3.0,
            "sud_opioid": 14.0,
            "sud_alcohol": 8.0,
            "bipolar": 7.0,
            "schizophrenia": 8.0,
        },
        "visits per treated member-year",
        "SAMHSA / HEDIS follow-up measures; claims studies",
        "assumption",
    ),
    _r(
        "bh.treated_share",
        0.70,
        "share of coded behavioural conditions with treatment in the year",
        "NSDUH 2021 (about 50-70% of adults with MDE received treatment)",
        "assumption",
    ),
    _r(
        "bh.admit_per_year",
        {
            "depression": 0.008,
            "bipolar": 0.05,
            "schizophrenia": 0.10,
            "sud_opioid": 0.04,
            "sud_alcohol": 0.03,
        },
        "psychiatric or SUD admission per member-year",
        "HCUP NIS mental health and substance use stays",
        "assumption",
    ),
    # ---- pregnancy --------------------------------------------------------------------------
    _r(
        "preg.births_per_1000",
        {"15-19": 13.9, "20-24": 56.7, "25-29": 91.0, "30-34": 95.4, "35-39": 54.5, "40-44": 12.0},
        "live births per 1000 women a year",
        f"{_NCHS} NVSS Births: Provisional Data for 2022",
    ),
    _r(
        "preg.cesarean_rate",
        0.321,
        "share of deliveries by cesarean",
        f"{_NCHS} NVSS Births: Provisional Data for 2022 (32.1%)",
    ),
    _r(
        "preg.gdm_rate",
        0.08,
        "gestational diabetes in pregnancy",
        "CDC Diabetes and Pregnancy (2% to 10% of pregnancies; 8.3% of births 2021)",
        "cited",
    ),
    _r(
        "preg.htn_rate",
        0.075,
        "hypertensive disorder of pregnancy",
        f"{_NCHS} NVSS 2021 (gestational hypertension 7-8% of births)",
    ),
    _r(
        "preg.prenatal_visits",
        12,
        "prenatal visits per pregnancy",
        "ACOG schedule (about 12-14 visits); CDC PRAMS",
        "cited",
    ),
    _r(
        "preg.preterm_rate",
        0.104,
        "preterm birth (earlier delivery, longer stay)",
        f"{_NCHS} NVSS 2022 (10.4%)",
    ),
    _r(
        "preg.high_risk_age",
        35,
        "age at which advanced maternal age is coded",
        "ACOG Committee Opinion 2022 (35 and older)",
    ),
    _r(
        "preg.medicaid_births_share",
        0.41,
        "Medicaid financed share of births",
        "CDC NVSS / KFF Births Financed by Medicaid (about 41%)",
    ),
    # ---- cancer (annual incidence per 100,000, selected age bands) ---------------------------
    _r(
        "cancer.breast_f_incidence",
        {"35-44": 120, "45-54": 250, "55-64": 330, "65-74": 400, "75+": 380},
        "per 100k women a year",
        "NCI SEER Cancer Stat Facts: Female Breast Cancer 2016-2020",
    ),
    _r(
        "cancer.prostate_incidence",
        {"45-54": 60, "55-64": 400, "65-74": 780, "75+": 560},
        "per 100k men a year",
        "NCI SEER Cancer Stat Facts: Prostate Cancer 2016-2020",
    ),
    _r(
        "cancer.colorectal_incidence",
        {"35-44": 18, "45-54": 55, "55-64": 85, "65-74": 160, "75+": 230},
        "per 100k a year",
        "NCI SEER Cancer Stat Facts: Colorectal Cancer 2016-2020",
    ),
    _r(
        "cancer.lung_incidence",
        {"45-54": 22, "55-64": 85, "65-74": 230, "75+": 260},
        "per 100k a year",
        "NCI SEER Cancer Stat Facts: Lung and Bronchus Cancer 2016-2020",
    ),
    _r(
        "cancer.chemo_share",
        {"breast": 0.45, "prostate": 0.10, "colon": 0.50, "lung": 0.65},
        "share treated with systemic therapy",
        "NCI SEER-Medicare treatment patterns",
        "assumption",
    ),
    _r(
        "cancer.surgery_share",
        {"breast": 0.88, "prostate": 0.40, "colon": 0.85, "lung": 0.30},
        "share having cancer surgery",
        "NCI SEER treatment statistics",
        "assumption",
    ),
    # ---- prevention and screening -----------------------------------------------------------
    _r(
        "prev.wellness_visit",
        {"commercial": 0.50, "ma": 0.68, "medicaid": 0.45},
        "annual preventive visit by plan",
        "NCQA HEDIS WCV/AAP, CMS AWV utilisation (about 40% of Medicare)",
        "assumption",
    ),
    _r(
        "prev.mammogram_biennial",
        0.74,
        "women 50-74 screened in 2 years",
        "NCQA HEDIS BCS-E (commercial about 75%)",
    ),
    _r(
        "prev.colorectal_10y",
        0.68,
        "adults 45-75 up to date",
        "NCQA HEDIS COL-E (commercial about 66-70%)",
    ),
    _r("prev.cervical_3y", 0.74, "women 21-64 screened", "NCQA HEDIS CCS (about 74%)"),
    _r(
        "prev.psa_screen_annual",
        0.12,
        "men 55-69 screened per year",
        "CDC NHIS 2018 (about 35% screened in the past 12 months age 50+ in older cohorts; falling)",
        "assumption",
    ),
    # ---- background utilisation -------------------------------------------------------------
    _r(
        "util.acute_visit_per_year",
        {
            "0-17": 1.2,
            "18-34": 0.7,
            "35-44": 0.8,
            "45-54": 0.9,
            "55-64": 1.0,
            "65-74": 1.2,
            "75+": 1.4,
        },
        "non-respiratory acute office visits",
        "NAMCS 2019 (3.0 office visits per person a year, about a third acute)",
        "assumption",
    ),
    _r(
        "util.ed_per_1000",
        {"commercial": 95, "ma": 230, "medicaid": 430},
        "background (injury, pain, symptom) ED visits per 1000 a year",
        "NCHS NHAMCS 2019/2021 ED rates by insurance (Medicaid about 3x private)",
        "assumption",
        "condition-driven ED visits are added on top; the measured total is checked against ED targets",
    ),
    _r(
        "util.ed_total_per_1000",
        {"commercial": (120, 260), "ma": (260, 520), "medicaid": (330, 760)},
        "acceptable total ED visits per 1000 a year",
        "NCHS NHAMCS 2021; HCCI Health Care Cost and Utilization Report (commercial about 140-160)",
    ),
    _r(
        "util.admit_total_per_1000",
        {"commercial": (30, 85), "ma": (150, 330), "medicaid": (40, 130)},
        "acceptable total inpatient admissions per 1000 a year",
        "HCCI HCCUR 2021 (commercial about 55-65); MedPAC Data Book (Medicare about 250-300); KFF Medicaid",
    ),
    _r(
        "util.admit_background_per_1000",
        {
            "0-17": 4.0,
            "18-34": 6.0,
            "35-44": 9.0,
            "45-54": 14.0,
            "55-64": 22.0,
            "65-74": 55.0,
            "75+": 110.0,
        },
        "background medical/surgical admissions per 1000",
        "HCUP NIS 2020 age-specific discharge rates excluding delivery",
        "assumption",
    ),
    _r(
        "util.frailty_shape",
        0.30,
        "gamma shape of the member utilisation multiplier (mean 1)",
        "tuned so the top 5% of members carry about half of spend (MEPS)",
        "assumption",
    ),
    _r(
        "util.top5_share",
        (0.38, 0.62),
        "acceptable share of total allowed spend by the top 5% of members",
        "AHRQ MEPS Statistical Brief 2021 (top 5% about 50%, top 1% about 22%)",
    ),
    _r(
        "util.top1_share",
        (0.14, 0.36),
        "acceptable share by the top 1%",
        "AHRQ MEPS Statistical Brief 2021 (about 22%)",
    ),
    _r(
        "util.readmit_30d",
        (0.0, 0.15),
        "acceptable 30-day all-cause readmission among admissions",
        "CMS HRRP / HCUP (all-cause 30-day about 14% Medicare, 8-10% commercial)",
    ),
    _r(
        "util.readmit_prob",
        {"commercial": 0.05, "ma": 0.11, "medicaid": 0.08},
        "probability a stay is followed by a 30-day readmission",
        "HCUP Statistical Brief 248 (30-day readmission about 14% all payers, higher in older)",
        "assumption",
    ),
    _r(
        "util.death_in_hospital_share",
        0.40,
        "deaths that occur in an inpatient stay",
        "CDC NCHS deaths by place of death (about 30% hospital)",
        "assumption",
    ),
    _r(
        "mortality.annual",
        _by_band(0.0003, 0.001, 0.0026, 0.0055, 0.012, 0.025, 0.075),
        "all-cause annual probability of death",
        "CDC NCHS United States Life Tables 2021 (insured populations are healthier; scale 0.8)",
    ),
    _r(
        "mortality.insured_scale",
        0.8,
        "multiplier for an insured population",
        "NCHS life tables vs insured cohort studies",
        "assumption",
    ),
    # ---- prices and cost sharing ------------------------------------------------------------
    _r(
        "price.payer_multiplier",
        {
            "commercial_pro": 1.40,
            "commercial_facility": 2.40,
            "commercial_lab": 1.80,
            "ma": 1.00,
            "medicaid": 0.72,
        },
        "allowed as a multiple of the Medicare-like base",
        "RAND Hospital Price Transparency Study (commercial 224% of Medicare overall; professional about 140%); Urban Institute Medicaid-to-Medicare fee index (72%)",
    ),
    _r(
        "price.region_factor",
        {"high": 1.12, "mid": 1.00, "low": 0.92},
        "regional price factor",
        "CMS PFS GPCI range (0.87-1.20 combined)",
        "assumption",
    ),
    _r(
        "price.noise_sigma",
        0.08,
        "lognormal sigma of provider contracted-rate variation",
        "FAIR Health / Health Care Cost Institute price variation within a market",
        "assumption",
    ),
    _r(
        "price.billed_markup",
        {"commercial": 3.0, "ma": 2.5, "medicaid": 2.2},
        "billed charge as a multiple of allowed",
        "Medicare cost report charge-to-cost ratios; HCCI charge markups",
        "assumption",
    ),
    _r(
        "price.drg_base_rate",
        7800.0,
        "Medicare-like base rate per DRG weight 1.0, USD (operating, capital and typical add-ons)",
        "CMS IPPS FY2023 national standardised amount (about $6.5-7k) plus capital and IME/DSH add-ons (about 15-20%)",
    ),
    _r(
        "price.drg_case_sigma",
        0.50,
        "lognormal sigma of the allowed amount within a DRG (case mix, implants, ICU days)",
        "HCUP cost variation within MS-DRG (coefficient of variation about 0.5-1.0 of case cost)",
        "assumption",
    ),
    _r(
        "price.outlier_per_diem",
        700.0,
        "Medicare-like outlier per-diem for days beyond twice the DRG geometric mean stay, USD",
        "CMS IPPS cost-outlier policy (marginal cost factor 80% of costs above the fixed-loss threshold)",
        "assumption",
    ),
    _r(
        "plan.oop_max_commercial",
        {2022: 8700, 2023: 9100, 2024: 9450},
        "ACA out-of-pocket limit, individual, USD",
        "HealthCare.gov / CMS Notice of Benefit and Payment Parameters",
    ),
    _r(
        "plan.moop_ma",
        {2022: 7550, 2023: 8300, 2024: 8850},
        "MA in-network MOOP limit, USD",
        "CMS Medicare Advantage rate announcements 2022-2024",
    ),
    _r(
        "plan.hsa_min_deductible",
        {2022: 1400, 2023: 1500, 2024: 1600},
        "HDHP minimum deductible, individual, USD",
        "IRS Rev. Proc. 2021-25, 2022-24, 2023-23",
    ),
    _r(
        "net.in_network_share",
        {"commercial": 0.92, "ma": 0.95, "medicaid": 0.97},
        "share of providers/claims in network",
        "KFF Employer Health Benefits Survey; Kaiser analysis of OON billing (about 5-10%)",
        "assumption",
    ),
    # ---- claim lifecycle ---------------------------------------------------------------------
    _r(
        "claim.initial_denial",
        {"commercial": 0.08, "ma": 0.07, "medicaid": 0.11},
        "share of claims denied on first adjudication",
        "Premier Inc 2022 and Change Healthcare Denials Index (about 10-12% initial denial); KFF MA/ACA denial data (about 7-19%)",
        "assumption",
    ),
    _r(
        "claim.resubmit_share",
        0.45,
        "share of denied claims corrected and resubmitted",
        "MGMA/AHA denial management surveys (about 45-65% reworked)",
        "assumption",
    ),
    _r(
        "claim.resubmit_paid_share",
        0.60,
        "share of resubmissions that are paid",
        "HFMA denial recovery studies (about 60-65% overturned)",
        "assumption",
    ),
    _r(
        "claim.adjust_share",
        0.02,
        "paid claims later replaced (late adjustment)",
        "payer operations benchmarks (1-3%)",
        "assumption",
    ),
    _r(
        "claim.reversal_share",
        0.006,
        "paid claims voided",
        "payer operations benchmarks (0.3-1%)",
        "assumption",
    ),
    _r(
        "claim.duplicate_share",
        0.008,
        "claims re-sent as exact duplicates",
        "CAQH Index 2022 (duplicate submissions below 1%)",
        "assumption",
    ),
    _r(
        "claim.lag_days",
        (7, 45),
        "days from service to receipt (range)",
        "CMS timely filing practice; payer benchmarks",
        "assumption",
    ),
    _r(
        "claim.pa_missing",
        0.04,
        "prior-authorisation-required services billed without one",
        "AMA prior authorisation survey 2022; CMS MA prior-auth data",
        "assumption",
    ),
    # ---- pharmacy -----------------------------------------------------------------------------
    _r(
        "rx.primary_nonadherence",
        {"generic": 0.07, "brand": 0.16},
        "new prescriptions never filled",
        "Fischer et al, J Gen Intern Med 2010 (24% of e-prescriptions never filled); recent PBM data (about 10-15%)",
        "assumption",
    ),
    _r(
        "rx.monthly_discontinue",
        {
            "statin": 0.045,
            "antihypertensive": 0.030,
            "antidiabetic": 0.030,
            "inhaler": 0.040,
            "antidepressant": 0.055,
            "other": 0.035,
        },
        "monthly probability of abandoning a maintenance drug",
        "published persistence studies (1-year statin persistence about 50-60%; antihypertensives 60-75%)",
        "assumption",
    ),
    _r(
        "rx.pdc_target",
        0.60,
        "share of maintenance users with PDC >= 80% (acceptable band 0.40-0.80)",
        "CMS Part D Star Ratings adherence measures (national averages 80-88% for statin/RAS/diabetes among continuous users); commercial about 55-65% of new users",
        "assumption",
    ),
    _r(
        "rx.refill_delay_days",
        (0.0, 0.7),
        "lognormal (mu, sigma) of days late for a refill",
        "pharmacy refill timing studies",
        "assumption",
    ),
    _r(
        "rx.early_refill",
        0.05,
        "refills made 20% or more early",
        "NCPDP refill-too-soon reject data (2-6%)",
        "assumption",
    ),
    _r(
        "rx.reject_rate",
        {"commercial": 0.045, "ma": 0.05, "medicaid": 0.06},
        "share of submissions rejected",
        "NCPDP/Surescripts and PBM benchmarks (about 5% of claims reject)",
        "assumption",
    ),
    _r(
        "rx.reversal_rate",
        0.025,
        "paid fills reversed (not picked up)",
        "NCPDP/Surescripts (about 2-4% returned to stock)",
    ),
    _r(
        "rx.mail_share",
        {"commercial": 0.18, "ma": 0.22, "medicaid": 0.02},
        "maintenance fills via mail order",
        "PCMA/IQVIA (mail order about 10-15% of retail scripts; 30%+ of maintenance for some plans)",
        "assumption",
    ),
    _r(
        "rx.generic_dispense_rate",
        0.90,
        "generic share of dispensed fills",
        "IQVIA Use of Medicines in the US 2022 (about 90%)",
    ),
    _r(
        "rx.copay_tier",
        {"commercial": (10, 35, 70, 0.25), "ma": (5, 15, 47, 0.25), "medicaid": (1, 2, 4, 0.0)},
        "copay tier 1, 2, 3 USD and tier 4 coinsurance",
        "KFF Employer Health Benefits Survey 2022 (average $11/$33/$58); CMS Part D benefit parameters; state Medicaid copay rules",
        "assumption",
    ),
    _r(
        "rx.dispensing_fee",
        {"commercial": 1.50, "ma": 1.30, "medicaid": 10.00},
        "dispensing fee, USD",
        "CMS Medicaid dispensing fee survey (about $10); PBM contracts",
        "assumption",
    ),
    _r(
        "rx.written_to_fill_days",
        (0, 3),
        "days between written and first fill",
        "Surescripts/PBM first-fill timing",
        "assumption",
    ),
    _r(
        "rx.adherence_beta",
        {
            "antidiabetic": (5.0, 1.6),
            "statin": (4.6, 1.7),
            "antihypertensive": (5.0, 1.6),
            "inhaler": (3.2, 1.8),
            "antidepressant": (3.6, 1.9),
            "other": (4.5, 1.7),
        },
        "Beta(a, b) of a member's refill adherence by drug group",
        "calibrated so the share of users with PDC >= 80% falls in the CMS Star Ratings / published range (rx.pdc_target)",
        "assumption",
    ),
    _r(
        "rx.renew_prob",
        0.92,
        "probability an expired prescription is renewed",
        "PBM persistence studies",
        "assumption",
    ),
    _r(
        "rx.pa_first_reject",
        0.45,
        "first fill of a prior-authorisation drug rejected for PA (code 75)",
        "CoverMyMeds Medication Access Report 2022",
        "assumption",
    ),
    _r(
        "rx.pa_approved",
        0.85,
        "PA requests approved",
        "CoverMyMeds Medication Access Report 2022 (about 80-90%)",
        "assumption",
    ),
    _r(
        "rx.ingredient_markup_brand_awp",
        1.0,
        "ingredient cost multiplier on the catalogue price (all drugs)",
        "model unit",
        "assumption",
    ),
)

RATES: dict[str, Rate] = {r.name: r for r in _RATES}


class Calibration:
    """Rates with optional overrides (for example from a captured shape)."""

    def __init__(self, overrides: Mapping[str, Any] | None = None) -> None:
        unknown = set(overrides or {}) - set(RATES)
        if unknown:
            raise KeyError(f"unknown calibration rates: {sorted(unknown)}")
        self.overrides = dict(overrides or {})
        self.used: set[str] = set()

    def get(self, name: str) -> Any:
        self.used.add(name)
        return self.overrides.get(name, RATES[name].value)

    def with_overrides(self, extra: Mapping[str, Any]) -> Calibration:
        return Calibration({**self.overrides, **extra})


def _fmt(value: Any) -> str:
    if isinstance(value, dict):
        return ", ".join(f"{k}: {_fmt(v)}" for k, v in value.items())
    if isinstance(value, tuple):
        return (
            "–".join(_fmt(v) for v in value)
            if len(value) == 2
            else "(" + ", ".join(_fmt(v) for v in value) + ")"
        )
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def render_markdown() -> str:
    lines = [
        "| Rate | Value | Unit | Source | Status |",
        "|---|---|---|---|---|",
    ]
    for r in RATES.values():
        status = "cited `[VERIFY]`" if r.status == "cited" else "assumption"
        note = f" {r.note}" if r.note else ""
        lines.append(f"| `{r.name}` | {_fmt(r.value)} | {r.unit} | {r.source}.{note} | {status} |")
    return "\n".join(lines)
