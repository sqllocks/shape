# ruff: noqa: E501
"""Reference sets besides ICD-10-CM: place of service, DRG, ICD-10-PCS, CARC/RARC, NCPDP reject
codes, NUCC taxonomy and the HCC seed.

Open sets only (CMS, X12/WPC, NCPDP public code lists, NUCC).  Type-of-bill and revenue codes are
NUBC-licensed and CPT is AMA-licensed: they come from a bring-your-own table (``byo.py``), never
from here.  Everything here is a seed behind :class:`CodeBook`; the codes lane's assets replace
it, and DRG weights and geometric mean lengths of stay are approximate (``[VERIFY]`` against the
CMS Table 5 of the fiscal year in force)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

FAR = date(9999, 12, 31)


@dataclass(frozen=True, slots=True)
class Pos:
    code: str
    name: str
    facility: bool  # a facility rate applies (hospital, ASC, SNF ...)
    effective: date = date(2015, 10, 1)


POS: dict[str, Pos] = {
    p.code: p
    for p in (
        Pos("02", "Telehealth", False, date(2017, 1, 1)),
        Pos("10", "Telehealth provided in patient's home", False, date(2022, 1, 1)),
        Pos("11", "Office", False),
        Pos("12", "Home", False),
        Pos("19", "Off campus-outpatient hospital", True),
        Pos("20", "Urgent care facility", False),
        Pos("21", "Inpatient hospital", True),
        Pos("22", "On campus-outpatient hospital", True),
        Pos("23", "Emergency room - hospital", True),
        Pos("24", "Ambulatory surgical center", True),
        Pos("31", "Skilled nursing facility", True),
        Pos("41", "Ambulance - land", False),
        Pos("49", "Independent clinic", False),
        Pos("50", "Federally qualified health center", False),
        Pos("51", "Inpatient psychiatric facility", True),
        Pos("65", "End-stage renal disease treatment facility", False),
        Pos("81", "Independent laboratory", False),
    )
}


def pos_valid_on(code: str, day: date) -> bool:
    return code in POS and POS[code].effective <= day


@dataclass(frozen=True, slots=True)
class Drg:
    code: str
    title: str
    kind: str  # "M" medical, "P" surgical (maternity DRGs are "M"/"P" as CMS assigns)
    weight: float  # approximate relative weight
    gmlos: float  # approximate geometric mean length of stay, days
    los_min: int
    los_max: int  # plausible ceiling for a generated stay


DRG: dict[str, Drg] = {
    d.code: d
    for d in (
        Drg("193", "Simple pneumonia and pleurisy with MCC", "M", 1.30, 4.6, 2, 21),
        Drg("194", "Simple pneumonia and pleurisy with CC", "M", 0.90, 3.6, 1, 14),
        Drg("195", "Simple pneumonia and pleurisy without CC/MCC", "M", 0.65, 2.7, 1, 9),
        Drg("190", "Chronic obstructive pulmonary disease with MCC", "M", 1.15, 4.1, 2, 18),
        Drg("191", "Chronic obstructive pulmonary disease with CC", "M", 0.85, 3.4, 1, 12),
        Drg("192", "Chronic obstructive pulmonary disease without CC/MCC", "M", 0.68, 2.7, 1, 8),
        Drg("291", "Heart failure and shock with MCC", "M", 1.35, 4.0, 2, 20),
        Drg("292", "Heart failure and shock with CC", "M", 0.88, 3.3, 1, 13),
        Drg("293", "Heart failure and shock without CC/MCC", "M", 0.60, 2.4, 1, 8),
        Drg(
            "870",
            "Septicemia or severe sepsis with mechanical ventilation >96 hours",
            "M",
            4.90,
            13.0,
            5,
            60,
        ),
        Drg(
            "871",
            "Septicemia or severe sepsis without MV >96 hours with MCC",
            "M",
            1.85,
            5.0,
            2,
            28,
        ),
        Drg(
            "872",
            "Septicemia or severe sepsis without MV >96 hours without MCC",
            "M",
            1.00,
            3.6,
            1,
            16,
        ),
        Drg("689", "Kidney and urinary tract infections with MCC", "M", 1.10, 3.7, 2, 16),
        Drg("690", "Kidney and urinary tract infections without MCC", "M", 0.79, 2.9, 1, 10),
        Drg("637", "Diabetes with MCC", "M", 1.30, 4.0, 2, 16),
        Drg("638", "Diabetes with CC", "M", 0.80, 3.0, 1, 10),
        Drg("639", "Diabetes without CC/MCC", "M", 0.60, 2.4, 1, 7),
        Drg("682", "Renal failure with MCC", "M", 1.50, 4.2, 2, 20),
        Drg("683", "Renal failure with CC", "M", 0.90, 3.2, 1, 12),
        Drg("684", "Renal failure without CC/MCC", "M", 0.62, 2.5, 1, 8),
        Drg("309", "Cardiac arrhythmia and conduction disorders with CC", "M", 0.80, 2.9, 1, 10),
        Drg(
            "310",
            "Cardiac arrhythmia and conduction disorders without CC/MCC",
            "M",
            0.55,
            2.1,
            1,
            6,
        ),
        Drg("603", "Cellulitis without MCC", "M", 0.82, 3.4, 1, 11),
        Drg("885", "Psychoses", "M", 1.30, 6.5, 2, 30),
        Drg(
            "897",
            "Alcohol, drug abuse or dependence without rehabilitation therapy without MCC",
            "M",
            0.75,
            3.8,
            1,
            12,
        ),
        Drg(
            "343",
            "Appendectomy without complicated principal diagnosis without CC/MCC",
            "P",
            0.97,
            1.9,
            1,
            6,
        ),
        Drg("419", "Laparoscopic cholecystectomy without CDE without CC/MCC", "P", 0.95, 2.0, 1, 7),
        Drg("418", "Laparoscopic cholecystectomy without CDE with CC", "P", 1.40, 3.2, 1, 11),
        Drg(
            "807",
            "Vaginal delivery without sterilization or D&C without CC/MCC",
            "M",
            0.72,
            2.1,
            1,
            5,
        ),
        Drg("806", "Vaginal delivery without sterilization or D&C with CC", "M", 0.93, 2.7, 1, 7),
        Drg("788", "Cesarean section without sterilization without CC/MCC", "P", 1.00, 3.0, 2, 8),
        Drg("787", "Cesarean section without sterilization with CC", "P", 1.12, 3.5, 2, 10),
        Drg("791", "Prematurity with major problems", "M", 3.90, 15.0, 6, 70),
        Drg("792", "Prematurity without major problems", "M", 1.55, 6.8, 3, 21),
        Drg("795", "Normal newborn", "M", 0.17, 2.1, 1, 5),
        Drg("582", "Mastectomy for malignancy without CC/MCC", "P", 1.25, 1.7, 1, 5),
        Drg("707", "Major male pelvic procedures without CC/MCC", "P", 1.55, 1.9, 1, 5),
        Drg("331", "Major small and large bowel procedures without CC/MCC", "P", 1.75, 5.4, 2, 16),
        Drg("280", "Acute myocardial infarction, discharged alive with MCC", "M", 1.65, 4.5, 2, 16),
        Drg("281", "Acute myocardial infarction, discharged alive with CC", "M", 0.97, 3.0, 1, 10),
        Drg(
            "282",
            "Acute myocardial infarction, discharged alive without CC/MCC",
            "M",
            0.75,
            2.1,
            1,
            7,
        ),
        Drg(
            "064", "Intracranial hemorrhage or cerebral infarction with MCC", "M", 1.85, 4.9, 2, 18
        ),
        Drg(
            "065",
            "Intracranial hemorrhage or cerebral infarction with CC or tPA in 24 hours",
            "M",
            1.00,
            3.2,
            1,
            12,
        ),
        Drg(
            "066",
            "Intracranial hemorrhage or cerebral infarction without CC/MCC",
            "M",
            0.70,
            2.4,
            1,
            8,
        ),
        Drg(
            "469",
            "Major hip and knee joint replacement or reattachment of lower extremity with MCC or total ankle replacement",
            "P",
            3.10,
            5.0,
            2,
            16,
        ),
        Drg(
            "470",
            "Major hip and knee joint replacement or reattachment of lower extremity without MCC",
            "P",
            1.95,
            2.3,
            1,
            7,
        ),
        Drg("164", "Major chest procedures without CC/MCC", "P", 2.50, 4.0, 2, 12),
    )
}

# DRG family by logical group; the tier suffix is chosen by CC/MCC count (severity).
DRG_FAMILIES: dict[str, tuple[str, str, str]] = {
    "pneumonia": ("193", "194", "195"),
    "copd": ("190", "191", "192"),
    "heart_failure": ("291", "292", "293"),
    "sepsis": ("871", "872", "872"),
    "sepsis_ventilated": ("870", "870", "870"),
    "uti": ("689", "690", "690"),
    "diabetes": ("637", "638", "639"),
    "renal_failure": ("682", "683", "684"),
    "arrhythmia": ("309", "309", "310"),
    "cellulitis": ("603", "603", "603"),
    "psychosis": ("885", "885", "885"),
    "substance": ("897", "897", "897"),
    "appendectomy": ("343", "343", "343"),
    "cholecystectomy": ("418", "418", "419"),
    "vaginal_delivery": ("806", "806", "807"),
    "cesarean": ("787", "787", "788"),
    "newborn": ("795", "795", "795"),
    "premature": ("792", "792", "792"),
    "very_premature": ("791", "791", "791"),
    "mastectomy": ("582", "582", "582"),
    "prostatectomy": ("707", "707", "707"),
    "colectomy": ("331", "331", "331"),
    "lobectomy": ("164", "164", "164"),
    "mi": ("280", "281", "282"),
    "joint": ("469", "470", "470"),
    "stroke": ("064", "065", "066"),
}

MCC_CODES = frozenset({"J96.01", "J96.00", "R65.20", "A41.9", "I21.4", "J18.9", "J12.82", "I63.9"})
CC_CODES = frozenset(
    {
        "N17.9",
        "O72.1",
        "I50.22",
        "I50.9",
        "N18.4",
        "N18.5",
        "N18.6",
        "J44.1",
        "E11.65",
        "E10.10",
        "J96.00",
        "E11.621",
        "I48.91",
        "C78.00",
        "C79.51",
        "F33.2",
        "E66.01",
    }
)


@dataclass(frozen=True, slots=True)
class Pcs:
    code: str
    desc: str


PCS: dict[str, Pcs] = {
    p.code: p
    for p in (
        Pcs("0DTJ4ZZ", "Resection of appendix, percutaneous endoscopic approach"),
        Pcs("0FT44ZZ", "Resection of gallbladder, percutaneous endoscopic approach"),
        Pcs("10E0XZZ", "Delivery of products of conception, external approach"),
        Pcs("10D00Z1", "Extraction of products of conception, low, open approach"),
        Pcs(
            "5A1D70Z", "Performance of urinary filtration, intermittent, less than 6 hours per day"
        ),
        Pcs("5A1935Z", "Respiratory ventilation, less than 24 consecutive hours"),
        Pcs("5A1955Z", "Respiratory ventilation, greater than 96 consecutive hours"),
        Pcs("5A1945Z", "Respiratory ventilation, 24-96 consecutive hours"),
        Pcs("0HTU0ZZ", "Resection of right breast, open approach"),
        Pcs("0HTV0ZZ", "Resection of left breast, open approach"),
        Pcs("0SRC0JZ", "Replacement of right knee joint with synthetic substitute, open approach"),
        Pcs("0SRD0JZ", "Replacement of left knee joint with synthetic substitute, open approach"),
        Pcs("0SR90JZ", "Replacement of right hip joint with synthetic substitute, open approach"),
        Pcs("0SRB0JZ", "Replacement of left hip joint with synthetic substitute, open approach"),
        Pcs("0VT00ZZ", "Resection of prostate, open approach"),
        Pcs("0DTK0ZZ", "Resection of ascending colon, open approach"),
        Pcs("0BTC0ZZ", "Resection of right upper lung lobe, open approach"),
    )
}


@dataclass(frozen=True, slots=True)
class Reason:
    code: str
    desc: str


# Short labels in our own words; the official X12 code lists (CARC, RARC) are copyrighted, so their
# text is bring-your-own (``byo.py`` table ``carc`` / ``rarc``) and the codes alone are used here.
CARC: dict[str, Reason] = {
    r.code: r
    for r in (
        Reason("1", "deductible applied"),
        Reason("2", "coinsurance applied"),
        Reason("3", "copayment applied"),
        Reason("4", "procedure and modifier do not agree"),
        Reason("16", "claim is missing information or has a billing error"),
        Reason("18", "exact duplicate of a claim already received"),
        Reason("22", "another payer may be primary (coordination of benefits)"),
        Reason("26", "service before coverage began"),
        Reason("27", "service after coverage ended"),
        Reason("29", "filing time limit passed"),
        Reason("45", "contractual adjustment to the contracted rate"),
        Reason("50", "not medically necessary under the plan's criteria"),
        Reason("96", "service not covered"),
        Reason("97", "included in the payment for another service"),
        Reason("109", "belongs to another payer"),
        Reason("197", "prior authorization absent"),
        Reason("204", "not a benefit of the member's plan"),
        Reason("242", "provider is not in the plan's network"),
    )
}

RARC: dict[str, Reason] = {
    r.code: r
    for r in (
        Reason("M51", "procedure code missing or invalid"),
        Reason("M76", "diagnosis missing or invalid"),
        Reason("N4", "prior insurance explanation of benefits missing or invalid"),
        Reason("N130", "see the plan benefit documents for restrictions"),
        Reason("N362", "units of service exceed the allowed maximum"),
        Reason("N522", "duplicate of a claim processed or in process"),
    )
}

# initial denial: (CARC, RARC or None, share of denials)
DENIAL_MIX: tuple[tuple[str, str | None, float], ...] = (
    ("197", "N130", 0.16),
    ("16", "M76", 0.14),
    ("50", None, 0.12),
    ("97", None, 0.10),
    ("18", "N522", 0.10),
    ("29", None, 0.05),
    ("22", "N4", 0.08),
    ("109", None, 0.04),
    ("204", "N130", 0.07),
    ("4", "M51", 0.06),
    ("242", None, 0.08),
)

NCPDP_REJECT: dict[str, str] = {
    "65": "Patient is not covered",
    "70": "Product/service not covered",
    "75": "Prior authorization required",
    "76": "Plan limitations exceeded",
    "79": "Refill too soon",
    "88": "DUR reject error",
    "608": "Step therapy, alternate drug therapy required prior to use of submitted product service ID",
}


@dataclass(frozen=True, slots=True)
class Specialty:
    key: str
    name: str
    kind: str  # "individual" or "organization"


SPECIALTY: dict[str, Specialty] = {
    s.key: s
    for s in (
        Specialty("family_medicine", "Family Medicine", "individual"),
        Specialty("internal_medicine", "Internal Medicine", "individual"),
        Specialty("pediatrics", "Pediatrics", "individual"),
        Specialty("obgyn", "Obstetrics & Gynecology", "individual"),
        Specialty("cardiology", "Cardiovascular Disease", "individual"),
        Specialty("endocrinology", "Endocrinology, Diabetes & Metabolism", "individual"),
        Specialty("nephrology", "Nephrology", "individual"),
        Specialty("pulmonology", "Pulmonary Disease", "individual"),
        Specialty("psychiatry", "Psychiatry", "individual"),
        Specialty("psychology", "Clinical Psychologist", "individual"),
        Specialty("oncology", "Medical Oncology", "individual"),
        Specialty("radiation_oncology", "Radiation Oncology", "individual"),
        Specialty("emergency_medicine", "Emergency Medicine", "individual"),
        Specialty("ophthalmology", "Ophthalmology", "individual"),
        Specialty("optometry", "Optometrist", "individual"),
        Specialty("podiatry", "Podiatrist", "individual"),
        Specialty("general_surgery", "General Surgery", "individual"),
        Specialty("orthopedics", "Orthopaedic Surgery", "individual"),
        Specialty("urology", "Urology", "individual"),
        Specialty("radiology", "Diagnostic Radiology", "individual"),
        Specialty("rheumatology", "Rheumatology", "individual"),
        Specialty("dermatology", "Dermatology", "individual"),
        Specialty("gastroenterology", "Gastroenterology", "individual"),
        Specialty("hospitalist", "Internal Medicine (Hospitalist)", "individual"),
        Specialty("hospital", "General Acute Care Hospital", "organization"),
        Specialty("laboratory", "Clinical Medical Laboratory", "organization"),
        Specialty("urgent_care", "Urgent Care Clinic", "organization"),
        Specialty("dialysis_center", "End-Stage Renal Disease Treatment Clinic", "organization"),
        Specialty("ambulance", "Ambulance, Land", "organization"),
        Specialty("dme_supplier", "Durable Medical Equipment & Medical Supplies", "organization"),
        Specialty("group_practice", "Multi-Specialty Group", "organization"),
        Specialty("retail_pharmacy", "Community/Retail Pharmacy", "organization"),
        Specialty("mail_pharmacy", "Mail Order Pharmacy", "organization"),
    )
}

PRIMARY_CARE = ("family_medicine", "internal_medicine", "pediatrics")

# CMS-HCC V28 seed: ICD-10-CM -> (HCC, label). Coefficients are illustrative weights, not the CMS
# published relative factors ([VERIFY]: replace with the codes lane's HCC asset).
HCC_LABEL: dict[int, str] = {
    17: "Cancer Metastatic to Lung, Liver, Brain, and Other Organs",
    18: "Cancer Metastatic to Bone, Other and Unspecified Metastatic Cancer",
    20: "Lung and Other Severe Cancers",
    22: "Bladder, Colorectal, and Other Cancers",
    23: "Prostate, Breast, and Other Cancers and Tumors",
    36: "Diabetes with Severe Acute Complications",
    37: "Diabetes with Chronic Complications",
    38: "Diabetes with Glycemic, Unspecified, or No Complications",
    48: "Morbid Obesity",
    137: "Drug Use Disorder, Moderate/Severe, or Drug Use with Non-Psychotic Complications",
    139: "Alcohol Use Disorder, Moderate/Severe, or Alcohol Use with Specified Non-Psychotic Complications",
    151: "Schizophrenia",
    154: "Bipolar Disorders without Psychosis",
    155: "Major Depression, Moderate or Severe, without Psychosis",
    213: "Cardio-Respiratory Failure and Shock",
    226: "Heart Failure, Except End-Stage and Acute",
    228: "Acute Myocardial Infarction",
    238: "Specified Heart Arrhythmias",
    249: "Ischemic or Unspecified Stroke",
    280: "Chronic Obstructive Pulmonary Disease, Interstitial Lung Disorders, and Other Chronic Lung Disorders",
    326: "Chronic Kidney Disease, Stage 5",
    327: "Chronic Kidney Disease, Severe (Stage 4)",
    328: "Chronic Kidney Disease, Moderate (Stage 3B)",
    329: "Chronic Kidney Disease, Moderate (Stage 3, Except 3B)",
}

HCC_OF: dict[str, int] = {
    **{c: 38 for c in ("E11.9", "E11.65", "E10.9", "E10.65", "E11.649", "E10.649")},
    **{c: 37 for c in ("E11.21", "E11.22", "E11.40", "E11.42", "E11.319", "E11.51", "E11.621")},
    "E10.10": 36,
    "E66.01": 48,
    "E66.813": 48,
    "F11.20": 137,
    "F10.20": 139,
    "F20.9": 151,
    "F31.9": 154,
    "F33.1": 155,
    "F33.2": 155,
    "J96.01": 213,
    "J96.00": 213,
    "I50.9": 226,
    "I50.22": 226,
    "I21.4": 228,
    "I48.91": 238,
    "I63.9": 249,
    "J44.9": 280,
    "J44.1": 280,
    "J44.0": 280,
    "N18.4": 327,
    "N18.5": 326,
    "N18.6": 326,
    "N18.30": 329,
    "N18.31": 329,
    "N18.32": 328,
    "C78.00": 17,
    "C79.51": 18,
    "C34.90": 20,
    "C18.9": 22,
    "C50.911": 23,
    "C50.912": 23,
    "C61": 23,
}

# lower number wins inside a group (CMS hierarchy)
HCC_HIERARCHY: tuple[tuple[int, ...], ...] = (
    (17, 18, 20, 22, 23),
    (36, 37, 38),
    (326, 327, 328, 329),
    (151, 154, 155),
    (137, 139),
)

# illustrative weights ([VERIFY] against the CMS V28 relative factors)
HCC_WEIGHT: dict[int, float] = {
    17: 4.2,
    18: 2.4,
    20: 1.1,
    22: 0.4,
    23: 0.2,
    36: 0.17,
    37: 0.17,
    38: 0.17,
    48: 0.19,
    137: 0.3,
    139: 0.2,
    151: 0.55,
    154: 0.3,
    155: 0.3,
    213: 0.7,
    226: 0.36,
    228: 0.5,
    238: 0.3,
    249: 0.3,
    280: 0.32,
    326: 0.8,
    327: 0.5,
    328: 0.17,
    329: 0.12,
}
