# ruff: noqa: E501
"""ICD-10-CM seed set: code, description, billable flag, age and sex edits, effective and end dates.

This is a hand-curated subset of the CDC/NCHS ICD-10-CM tables (FY2016-FY2025) covering the codes
the clinical modules emit.  It stands behind :class:`CodeBook` so the full asset from the codes
lane can replace it; every entry is cross-checked against that asset when it is merged (see
``docs/plans/lane_status/HC-domain.md``).  Dates matter: the code set changes each 1 October, and
the seed keeps the pairs that exercise it (``N18.3`` -> ``N18.30``, ``M54.5`` -> ``M54.50``,
``R05`` -> ``R05.9``, ``R51`` -> ``R51.9``, ``F32.A``, ``E66.81x``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

FAR = date(9999, 12, 31)
_START = date(2015, 10, 1)


@dataclass(frozen=True, slots=True)
class Icd:
    code: str
    desc: str
    billable: bool = True
    sex: str | None = None  # "F" or "M" when the code only fits one sex
    age_min: int = 0
    age_max: int = 124
    effective: date = _START
    end: date = FAR

    @property
    def category(self) -> str:
        return self.code[:3]

    @property
    def chapter(self) -> int:
        return _chapter(self.code)

    def valid_on(self, day: date) -> bool:
        return self.effective <= day <= self.end


def _chapter(code: str) -> int:
    head, num = code[0], code[1:3]
    if head in "AB":
        return 1
    if head == "C" or (head == "D" and num < "50"):
        return 2
    if head == "D":
        return 3
    simple = {"E": 4, "F": 5, "G": 6, "I": 9, "J": 10, "K": 11, "L": 12, "M": 13, "N": 14}
    simple.update({"O": 15, "P": 16, "Q": 17, "R": 18, "U": 22, "Z": 21})
    if head in simple:
        return simple[head]
    if head == "H":
        return 7 if num < "60" else 8
    if head in "ST":
        return 19
    return 20


def _d(text: str) -> date:
    return date.fromisoformat(text)


_RAW: list[tuple[str, str, dict[str, object]]] = [
    # diabetes
    ("E11", "Type 2 diabetes mellitus", {"billable": False}),
    ("E11.9", "Type 2 diabetes mellitus without complications", {}),
    ("E11.65", "Type 2 diabetes mellitus with hyperglycemia", {}),
    ("E11.21", "Type 2 diabetes mellitus with diabetic nephropathy", {}),
    ("E11.22", "Type 2 diabetes mellitus with diabetic chronic kidney disease", {}),
    ("E11.40", "Type 2 diabetes mellitus with diabetic neuropathy, unspecified", {}),
    ("E11.42", "Type 2 diabetes mellitus with diabetic polyneuropathy", {}),
    ("E11.319", "Type 2 diabetes with unspecified diabetic retinopathy without macular edema", {}),
    ("E11.51", "Type 2 diabetes with diabetic peripheral angiopathy without gangrene", {}),
    ("E11.621", "Type 2 diabetes mellitus with foot ulcer", {}),
    ("E11.649", "Type 2 diabetes mellitus with hypoglycemia without coma", {}),
    ("E10", "Type 1 diabetes mellitus", {"billable": False}),
    ("E10.9", "Type 1 diabetes mellitus without complications", {}),
    ("E10.65", "Type 1 diabetes mellitus with hyperglycemia", {}),
    ("E10.10", "Type 1 diabetes mellitus with ketoacidosis without coma", {}),
    ("E10.649", "Type 1 diabetes mellitus with hypoglycemia without coma", {}),
    ("Z79.4", "Long term (current) use of insulin", {}),
    ("Z79.84", "Long term (current) use of oral hypoglycemic drugs", {"effective": "2016-10-01"}),
    ("R73.03", "Prediabetes", {"effective": "2016-10-01"}),
    (
        "O24.410",
        "Gestational diabetes mellitus in pregnancy, diet controlled",
        {"sex": "F", "age_min": 10, "age_max": 55},
    ),
    (
        "O24.419",
        "Gestational diabetes mellitus in pregnancy, unspecified control",
        {"sex": "F", "age_min": 10, "age_max": 55},
    ),
    # hypertension and heart
    ("I10", "Essential (primary) hypertension", {}),
    ("I11.0", "Hypertensive heart disease with heart failure", {}),
    ("I12.9", "Hypertensive CKD with stage 1-4 chronic kidney disease or unspecified CKD", {}),
    (
        "I12.0",
        "Hypertensive CKD with stage 5 chronic kidney disease or end stage renal disease",
        {},
    ),
    (
        "I25.10",
        "Atherosclerotic heart disease of native coronary artery without angina pectoris",
        {"age_min": 18},
    ),
    ("I48.91", "Unspecified atrial fibrillation", {"age_min": 18}),
    ("I50.9", "Heart failure, unspecified", {"age_min": 18}),
    ("I50.22", "Chronic systolic (congestive) heart failure", {"age_min": 18}),
    ("I21.4", "Non-ST elevation (NSTEMI) myocardial infarction", {"age_min": 18}),
    ("I63.9", "Cerebral infarction, unspecified", {"age_min": 18}),
    # lipids, obesity, thyroid, GERD
    ("E78.5", "Hyperlipidemia, unspecified", {}),
    ("E78.00", "Pure hypercholesterolemia, unspecified", {}),
    ("E78.01", "Familial hypercholesterolemia", {}),
    ("E78.2", "Mixed hyperlipidemia", {}),
    ("E78.1", "Pure hyperglyceridemia", {}),
    ("E66.9", "Obesity, unspecified", {}),
    ("E66.01", "Morbid (severe) obesity due to excess calories", {}),
    ("E66.811", "Obesity, class 1", {"effective": "2024-10-01", "age_min": 2}),
    ("E66.812", "Obesity, class 2", {"effective": "2024-10-01", "age_min": 2}),
    ("E66.813", "Obesity, class 3", {"effective": "2024-10-01", "age_min": 2}),
    ("Z68.30", "Body mass index [BMI] 30.0-30.9, adult", {"age_min": 20}),
    ("Z68.35", "Body mass index [BMI] 35.0-35.9, adult", {"age_min": 20}),
    ("Z68.41", "Body mass index [BMI] 40.0-44.9, adult", {"age_min": 20}),
    ("E03.9", "Hypothyroidism, unspecified", {}),
    ("M06.9", "Rheumatoid arthritis, unspecified", {"age_min": 16}),
    ("L40.0", "Psoriasis vulgaris", {}),
    ("K50.90", "Crohn's disease, unspecified, without complications", {}),
    ("K51.90", "Ulcerative colitis, unspecified, without complications", {}),
    (
        "N40.1",
        "Benign prostatic hyperplasia with lower urinary tract symptoms",
        {"sex": "M", "age_min": 30},
    ),
    ("K21.9", "Gastro-esophageal reflux disease without esophagitis", {}),
    # kidney
    ("N18.1", "Chronic kidney disease, stage 1", {}),
    ("N18.2", "Chronic kidney disease, stage 2 (mild)", {}),
    ("N18.3", "Chronic kidney disease, stage 3 (moderate)", {"end": "2020-09-30"}),
    ("N18.30", "Chronic kidney disease, stage 3 unspecified", {"effective": "2020-10-01"}),
    ("N18.31", "Chronic kidney disease, stage 3a", {"effective": "2020-10-01"}),
    ("N18.32", "Chronic kidney disease, stage 3b", {"effective": "2020-10-01"}),
    ("N18.4", "Chronic kidney disease, stage 4 (severe)", {}),
    ("N18.5", "Chronic kidney disease, stage 5", {}),
    ("N18.6", "End stage renal disease", {}),
    ("N18.9", "Chronic kidney disease, unspecified", {}),
    ("Z99.2", "Dependence on renal dialysis", {}),
    ("N17.9", "Acute kidney failure, unspecified", {}),
    ("D63.1", "Anemia in chronic kidney disease", {}),
    ("N39.0", "Urinary tract infection, site not specified", {}),
    # respiratory chronic
    ("J45.909", "Unspecified asthma, uncomplicated", {}),
    ("J45.901", "Unspecified asthma with (acute) exacerbation", {}),
    ("J45.20", "Mild intermittent asthma, uncomplicated", {}),
    ("J45.21", "Mild intermittent asthma with (acute) exacerbation", {}),
    ("J45.30", "Mild persistent asthma, uncomplicated", {}),
    ("J45.31", "Mild persistent asthma with (acute) exacerbation", {}),
    ("J45.40", "Moderate persistent asthma, uncomplicated", {}),
    ("J45.41", "Moderate persistent asthma with (acute) exacerbation", {}),
    ("J44.9", "Chronic obstructive pulmonary disease, unspecified", {"age_min": 30}),
    ("J44.1", "Chronic obstructive pulmonary disease with (acute) exacerbation", {"age_min": 30}),
    ("J44.0", "COPD with (acute) lower respiratory infection", {"age_min": 30}),
    ("J96.01", "Acute respiratory failure with hypoxia", {}),
    ("J96.00", "Acute respiratory failure, unspecified whether with hypoxia or hypercapnia", {}),
    ("F17.210", "Nicotine dependence, cigarettes, uncomplicated", {"age_min": 10}),
    ("Z87.891", "Personal history of nicotine dependence", {}),
    ("R06.02", "Shortness of breath", {}),
    ("R05", "Cough", {"end": "2021-09-30"}),
    ("R05.9", "Cough, unspecified", {"effective": "2021-10-01"}),
    # respiratory acute
    ("J06.9", "Acute upper respiratory infection, unspecified", {}),
    ("J20.9", "Acute bronchitis, unspecified", {}),
    ("J02.9", "Acute pharyngitis, unspecified", {}),
    ("J02.0", "Streptococcal pharyngitis", {}),
    ("J01.90", "Acute sinusitis, unspecified", {}),
    ("J03.90", "Acute tonsillitis, unspecified", {}),
    (
        "J10.1",
        "Influenza due to other identified influenza virus with other respiratory manifestations",
        {},
    ),
    (
        "J11.1",
        "Influenza due to unidentified influenza virus with other respiratory manifestations",
        {},
    ),
    ("J18.9", "Pneumonia, unspecified organism", {}),
    ("J12.9", "Viral pneumonia, unspecified", {}),
    ("J12.82", "Pneumonia due to coronavirus disease 2019", {"effective": "2020-04-01"}),
    ("U07.1", "COVID-19", {"effective": "2020-04-01"}),
    ("J21.9", "Acute bronchiolitis, unspecified", {"age_max": 5}),
    ("J30.9", "Allergic rhinitis, unspecified", {}),
    ("H66.90", "Otitis media, unspecified, unspecified ear", {"age_max": 17}),
    ("R50.9", "Fever, unspecified", {}),
    ("B34.9", "Viral infection, unspecified", {}),
    ("A08.4", "Viral intestinal infection, unspecified", {}),
    # pregnancy and newborn
    (
        "Z34.00",
        "Encounter for supervision of normal first pregnancy, unspecified trimester",
        {"sex": "F", "age_min": 10, "age_max": 55},
    ),
    (
        "Z34.01",
        "Encounter for supervision of normal first pregnancy, first trimester",
        {"sex": "F", "age_min": 10, "age_max": 55},
    ),
    (
        "Z34.02",
        "Encounter for supervision of normal first pregnancy, second trimester",
        {"sex": "F", "age_min": 10, "age_max": 55},
    ),
    (
        "Z34.03",
        "Encounter for supervision of normal first pregnancy, third trimester",
        {"sex": "F", "age_min": 10, "age_max": 55},
    ),
    (
        "Z34.81",
        "Encounter for supervision of other normal pregnancy, first trimester",
        {"sex": "F", "age_min": 10, "age_max": 55},
    ),
    (
        "Z34.82",
        "Encounter for supervision of other normal pregnancy, second trimester",
        {"sex": "F", "age_min": 10, "age_max": 55},
    ),
    (
        "Z34.83",
        "Encounter for supervision of other normal pregnancy, third trimester",
        {"sex": "F", "age_min": 10, "age_max": 55},
    ),
    (
        "O09.90",
        "Supervision of high risk pregnancy, unspecified, unspecified trimester",
        {"sex": "F", "age_min": 10, "age_max": 55},
    ),
    (
        "O09.521",
        "Supervision of elderly multigravida, first trimester",
        {"sex": "F", "age_min": 35, "age_max": 55},
    ),
    (
        "O09.522",
        "Supervision of elderly multigravida, second trimester",
        {"sex": "F", "age_min": 35, "age_max": 55},
    ),
    (
        "O09.523",
        "Supervision of elderly multigravida, third trimester",
        {"sex": "F", "age_min": 35, "age_max": 55},
    ),
    (
        "O13.3",
        "Gestational [pregnancy-induced] hypertension without significant proteinuria, third trimester",
        {"sex": "F", "age_min": 10, "age_max": 55},
    ),
    (
        "O14.03",
        "Mild to moderate pre-eclampsia, third trimester",
        {"sex": "F", "age_min": 10, "age_max": 55},
    ),
    (
        "O80",
        "Encounter for full-term uncomplicated delivery",
        {"sex": "F", "age_min": 10, "age_max": 55},
    ),
    (
        "O82",
        "Encounter for cesarean delivery without indication",
        {"sex": "F", "age_min": 10, "age_max": 55},
    ),
    ("O72.1", "Other immediate postpartum hemorrhage", {"sex": "F", "age_min": 10, "age_max": 55}),
    ("Z37.0", "Single live birth", {"sex": "F", "age_min": 10, "age_max": 55}),
    (
        "Z39.2",
        "Encounter for routine postpartum follow-up",
        {"sex": "F", "age_min": 10, "age_max": 55},
    ),
    ("P07.31", "Preterm newborn, gestational age 28 completed weeks", {"age_max": 0}),
    ("P07.32", "Preterm newborn, gestational age 29 completed weeks", {"age_max": 0}),
    ("P07.33", "Preterm newborn, gestational age 30 completed weeks", {"age_max": 0}),
    ("P07.34", "Preterm newborn, gestational age 31 completed weeks", {"age_max": 0}),
    ("P07.35", "Preterm newborn, gestational age 32 completed weeks", {"age_max": 0}),
    ("P07.36", "Preterm newborn, gestational age 33 completed weeks", {"age_max": 0}),
    ("P07.37", "Preterm newborn, gestational age 34 completed weeks", {"age_max": 0}),
    ("P07.38", "Preterm newborn, gestational age 35 completed weeks", {"age_max": 0}),
    ("P07.39", "Preterm newborn, gestational age 36 completed weeks", {"age_max": 0}),
    ("Z38.00", "Single liveborn infant, delivered vaginally", {"age_max": 0}),
    ("Z38.01", "Single liveborn infant, delivered by cesarean", {"age_max": 0}),
    ("Z00.110", "Health examination for newborn under 8 days old", {"age_max": 0}),
    ("Z00.111", "Health examination for newborn 8 to 28 days old", {"age_max": 0}),
    # cancer and screening
    ("C50.911", "Malignant neoplasm of unspecified site of right female breast", {"sex": "F"}),
    ("C50.912", "Malignant neoplasm of unspecified site of left female breast", {"sex": "F"}),
    ("C61", "Malignant neoplasm of prostate", {"sex": "M", "age_min": 18}),
    ("C18.9", "Malignant neoplasm of colon, unspecified", {"age_min": 18}),
    (
        "C34.90",
        "Malignant neoplasm of unspecified part of unspecified bronchus or lung",
        {"age_min": 18},
    ),
    ("C78.00", "Secondary malignant neoplasm of unspecified lung", {"age_min": 18}),
    ("C79.51", "Secondary malignant neoplasm of bone", {"age_min": 18}),
    ("D12.6", "Benign neoplasm of colon, unspecified", {"age_min": 18}),
    ("R97.20", "Elevated prostate specific antigen [PSA]", {"sex": "M", "age_min": 18}),
    ("R92.8", "Other abnormal and inconclusive findings on diagnostic imaging of breast", {}),
    ("G89.3", "Neoplasm related pain (acute) (chronic)", {}),
    ("D64.81", "Anemia due to antineoplastic chemotherapy", {}),
    ("Z51.11", "Encounter for antineoplastic chemotherapy", {}),
    ("Z51.0", "Encounter for antineoplastic radiation therapy", {}),
    ("Z85.3", "Personal history of malignant neoplasm of breast", {}),
    ("Z85.46", "Personal history of malignant neoplasm of prostate", {"sex": "M"}),
    ("Z85.038", "Personal history of other malignant neoplasm of large intestine", {}),
    ("Z85.118", "Personal history of other malignant neoplasm of bronchus and lung", {}),
    (
        "Z12.31",
        "Encounter for screening mammogram for malignant neoplasm of breast",
        {"sex": "F", "age_min": 18},
    ),
    ("Z12.11", "Encounter for screening for malignant neoplasm of colon", {"age_min": 18}),
    (
        "Z12.4",
        "Encounter for screening for malignant neoplasm of cervix",
        {"sex": "F", "age_min": 18},
    ),
    (
        "Z12.5",
        "Encounter for screening for malignant neoplasm of prostate",
        {"sex": "M", "age_min": 18},
    ),
    ("R11.2", "Nausea with vomiting, unspecified", {}),
    # behavioral
    ("F32.9", "Major depressive disorder, single episode, unspecified", {"age_min": 5}),
    ("F32.A", "Depression, unspecified", {"effective": "2021-10-01", "age_min": 5}),
    ("F33.1", "Major depressive disorder, recurrent, moderate", {"age_min": 5}),
    (
        "F33.2",
        "Major depressive disorder, recurrent severe without psychotic features",
        {"age_min": 5},
    ),
    ("F41.1", "Generalized anxiety disorder", {"age_min": 3}),
    ("F41.9", "Anxiety disorder, unspecified", {"age_min": 3}),
    ("F43.10", "Post-traumatic stress disorder, unspecified", {"age_min": 3}),
    ("F10.20", "Alcohol dependence, uncomplicated", {"age_min": 12}),
    ("F10.10", "Alcohol abuse, uncomplicated", {"age_min": 12}),
    ("F11.20", "Opioid dependence, uncomplicated", {"age_min": 12}),
    ("F11.10", "Opioid abuse, uncomplicated", {"age_min": 12}),
    (
        "F90.9",
        "Attention-deficit hyperactivity disorder, unspecified type",
        {"age_min": 3, "age_max": 80},
    ),
    (
        "F90.2",
        "Attention-deficit hyperactivity disorder, combined type",
        {"age_min": 3, "age_max": 80},
    ),
    ("F84.0", "Autistic disorder", {"age_max": 40}),
    ("F31.9", "Bipolar disorder, unspecified", {"age_min": 10}),
    ("F20.9", "Schizophrenia, unspecified", {"age_min": 10}),
    ("R45.851", "Suicidal ideations", {}),
    ("G47.00", "Insomnia, unspecified", {}),
    # musculoskeletal, injury, pain
    ("M54.5", "Low back pain", {"end": "2021-09-30"}),
    ("M54.50", "Low back pain, unspecified", {"effective": "2021-10-01"}),
    ("M54.2", "Cervicalgia", {}),
    ("M25.561", "Pain in right knee", {}),
    ("M25.562", "Pain in left knee", {}),
    ("M17.11", "Unilateral primary osteoarthritis, right knee", {"age_min": 30}),
    ("M16.11", "Unilateral primary osteoarthritis, right hip", {"age_min": 30}),
    ("M16.12", "Unilateral primary osteoarthritis, left hip", {"age_min": 30}),
    ("H25.9", "Unspecified age-related cataract", {"age_min": 40}),
    ("M17.12", "Unilateral primary osteoarthritis, left knee", {"age_min": 30}),
    (
        "M81.0",
        "Age-related osteoporosis without current pathological fracture",
        {"sex": "F", "age_min": 45},
    ),
    ("S93.401A", "Sprain of unspecified ligament of right ankle, initial encounter", {}),
    ("S93.402A", "Sprain of unspecified ligament of left ankle, initial encounter", {}),
    ("S93.401D", "Sprain of unspecified ligament of right ankle, subsequent encounter", {}),
    (
        "S82.201A",
        "Unspecified fracture of shaft of right tibia, initial encounter for closed fracture",
        {},
    ),
    (
        "S82.201D",
        "Unspecified fracture of shaft of right tibia, subsequent encounter for closed fracture with routine healing",
        {},
    ),
    (
        "S72.001A",
        "Fracture of unspecified part of neck of right femur, initial encounter for closed fracture",
        {"age_min": 40},
    ),
    (
        "S72.001D",
        "Fracture of unspecified part of neck of right femur, subsequent encounter for closed fracture with routine healing",
        {"age_min": 40},
    ),
    ("S06.0X0A", "Concussion without loss of consciousness, initial encounter", {}),
    ("S61.411A", "Laceration without foreign body of right hand, initial encounter", {}),
    # symptoms and acute surgical
    ("R07.9", "Chest pain, unspecified", {}),
    ("R10.9", "Unspecified abdominal pain", {}),
    ("R51", "Headache", {"end": "2020-09-30"}),
    ("R51.9", "Headache, unspecified", {"effective": "2020-10-01"}),
    ("G43.909", "Migraine, unspecified, not intractable, without status migrainosus", {}),
    ("R42", "Dizziness and giddiness", {}),
    ("R55", "Syncope and collapse", {}),
    ("R53.83", "Other fatigue", {}),
    ("L03.90", "Cellulitis, unspecified", {}),
    ("L30.9", "Dermatitis, unspecified", {}),
    ("K35.80", "Unspecified acute appendicitis", {}),
    (
        "K80.20",
        "Calculus of gallbladder without cholecystitis without obstruction",
        {"age_min": 10},
    ),
    ("K81.0", "Acute cholecystitis", {"age_min": 10}),
    ("A41.9", "Sepsis, unspecified organism", {}),
    ("R65.20", "Severe sepsis without septic shock", {}),
    ("E86.0", "Dehydration", {}),
    ("D50.9", "Iron deficiency anemia, unspecified", {}),
    ("G40.909", "Epilepsy, unspecified, not intractable, without status epilepticus", {}),
    # status and encounters
    (
        "Z00.00",
        "Encounter for general adult medical examination without abnormal findings",
        {"age_min": 18},
    ),
    (
        "Z00.01",
        "Encounter for general adult medical examination with abnormal findings",
        {"age_min": 18},
    ),
    (
        "Z00.129",
        "Encounter for routine child health examination without abnormal findings",
        {"age_max": 17},
    ),
    (
        "Z00.121",
        "Encounter for routine child health examination with abnormal findings",
        {"age_max": 17},
    ),
    ("Z23", "Encounter for immunization", {}),
    ("Z13.31", "Encounter for screening for depression", {}),
    ("Z13.220", "Encounter for screening for lipoid disorders", {}),
    ("Z71.3", "Dietary counseling and surveillance", {}),
    ("Z79.82", "Long term (current) use of aspirin", {}),
    ("Z79.899", "Other long term (current) drug therapy", {}),
    ("Z79.01", "Long term (current) use of anticoagulants", {}),
]

_WEEKS = [("Z3A.01", "Less than 8 weeks gestation of pregnancy")] + [
    (f"Z3A.{w:02d}", f"{w} weeks gestation of pregnancy") for w in range(8, 43)
]


def _build() -> dict[str, Icd]:
    out: dict[str, Icd] = {}
    for code, desc, extra in _RAW:
        kw = dict(extra)
        for key in ("effective", "end"):
            if key in kw:
                kw[key] = _d(str(kw[key]))
        out[code] = Icd(code, desc, **kw)  # type: ignore[arg-type]
    for code, desc in _WEEKS:
        out[code] = Icd(code, desc, sex="F", age_min=10, age_max=55)
    return out


ICD10CM: dict[str, Icd] = _build()

# A logical concept maps to the code that is valid on the date of service.
CONCEPTS: dict[str, tuple[str, ...]] = {
    "low_back_pain": ("M54.5", "M54.50"),
    "cough": ("R05", "R05.9"),
    "headache": ("R51", "R51.9"),
    "ckd3": ("N18.3", "N18.30"),
    "depression_unsp": ("F32.9", "F32.A"),
    "obesity_class1": ("E66.9", "E66.811"),
    "obesity_class2": ("E66.9", "E66.812"),
    "obesity_class3": ("E66.01", "E66.813"),
}


def resolve(code_or_concept: str, day: date) -> str:
    """The valid code for a concept or code on ``day`` (a fixed code is returned unchanged)."""
    if code_or_concept in CONCEPTS:
        options = CONCEPTS[code_or_concept]
        chosen = options[0]
        for opt in options:
            if ICD10CM[opt].valid_on(day):
                chosen = opt
        return chosen
    return code_or_concept
