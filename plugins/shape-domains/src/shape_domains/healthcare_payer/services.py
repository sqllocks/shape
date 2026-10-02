"""Service catalog: what a claim line bills for.

A service with an open HCPCS Level II code carries it.  A service that is only billable with a
CPT code (office visit levels, labs, imaging, procedures) has ``hcpcs=None``: by default its line
carries the Shape service key (code system ``SHAPE-SVC``), and when the user supplies a licensed
CPT table (``byo.load_cpt``) the line carries the CPT code instead.  Descriptions are our own
words, never AMA descriptors.  ``base`` is an illustrative national Medicare-like allowed amount
in US dollars per unit (same order of magnitude as the CMS PFS/ASP; not a copy of any row)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

FAR = date(9999, 12, 31)


@dataclass(frozen=True, slots=True)
class Service:
    key: str
    hcpcs: str | None
    desc: str
    kind: str  # pro | lab | imaging | facility | drug | dme | transport | preventive | behavioral
    base: float
    pos: tuple[str, ...]  # places of service the line may carry
    specialties: tuple[str, ...]  # rendering specialties allowed to bill it
    units: int = 1
    pa: bool = False  # prior authorisation usually required
    sex: str | None = None
    age_min: int = 0
    age_max: int = 124
    effective: date = date(2015, 10, 1)


_PC = ("family_medicine", "internal_medicine", "pediatrics")
_OFFICE = ("11", "19", "22", "49", "50", "02", "10")
_ANY_MD = _PC + (
    "cardiology", "endocrinology", "nephrology", "pulmonology", "obgyn", "oncology", "urology",
    "psychiatry", "general_surgery", "orthopedics", "hospitalist", "rheumatology", "dermatology",
    "gastroenterology",
)


def _s(*a: object, **k: object) -> Service:
    return Service(*a, **k)  # type: ignore[arg-type]


_ITEMS: tuple[Service, ...] = (
    # --- visits (CPT-only): our own level names ---
    _s("EM_OFFICE_EST_2", None, "Established patient office visit, low complexity", "pro", 58.0, _OFFICE, _ANY_MD),
    _s("EM_OFFICE_EST_3", None, "Established patient office visit, moderate complexity", "pro", 92.0, _OFFICE, _ANY_MD),
    _s("EM_OFFICE_EST_4", None, "Established patient office visit, high complexity", "pro", 130.0, _OFFICE, _ANY_MD),
    _s("EM_OFFICE_NEW_3", None, "New patient office visit, moderate complexity", "pro", 125.0, _OFFICE, _ANY_MD),
    _s("EM_OFFICE_NEW_4", None, "New patient office visit, high complexity", "pro", 195.0, _OFFICE, _ANY_MD),
    _s("EM_TELE_EST", None, "Established patient telehealth visit", "pro", 75.0, ("02", "10"), _ANY_MD + ("psychology",), effective=date(2020, 3, 1)),
    _s("EM_PREV_ADULT", None, "Preventive visit, adult", "preventive", 128.0, ("11", "19", "22", "49", "50"), _PC + ("obgyn",), age_min=18),
    _s("EM_PREV_PEDS", None, "Preventive visit, child", "preventive", 105.0, ("11", "49", "50"), ("pediatrics", "family_medicine"), age_max=17),
    _s("EM_NEWBORN", None, "Newborn care, hospital day", "pro", 76.0, ("21",), ("pediatrics", "hospitalist")),
    _s("EM_ED_3", None, "Emergency department visit, moderate severity (professional)", "pro", 88.0, ("23",), ("emergency_medicine",)),
    _s("EM_ED_4", None, "Emergency department visit, high severity (professional)", "pro", 137.0, ("23",), ("emergency_medicine",)),
    _s("EM_ED_5", None, "Emergency department visit, critical (professional)", "pro", 205.0, ("23",), ("emergency_medicine",)),
    _s("EM_URGENT_3", None, "Urgent care visit, moderate complexity", "pro", 92.0, ("20",), _PC + ("emergency_medicine",)),
    _s("EM_INPT_INITIAL", None, "Inpatient initial hospital care", "pro", 205.0, ("21", "51"), ("hospitalist", "internal_medicine", "psychiatry", "cardiology", "pulmonology", "general_surgery", "obgyn", "oncology")),
    _s("EM_INPT_SUBSEQ", None, "Inpatient subsequent hospital care", "pro", 76.0, ("21", "51"), ("hospitalist", "internal_medicine", "psychiatry", "cardiology", "pulmonology", "general_surgery", "obgyn", "oncology")),
    _s("EM_INPT_DISCHARGE", None, "Hospital discharge day management", "pro", 100.0, ("21", "51"), ("hospitalist", "internal_medicine", "psychiatry", "cardiology", "pulmonology", "general_surgery", "obgyn", "oncology")),
    _s("EM_CRITICAL_CARE", None, "Critical care, first hour", "pro", 280.0, ("21", "23"), ("hospitalist", "pulmonology", "emergency_medicine", "cardiology")),
    # --- preventive and screening with open HCPCS codes ---
    _s("AWV_INITIAL", "G0438", "Annual wellness visit, initial (Medicare)", "preventive", 175.0, ("11", "19", "22", "49", "50", "02", "10"), _PC, age_min=65),
    _s("AWV_SUBSEQ", "G0439", "Annual wellness visit, subsequent (Medicare)", "preventive", 118.0, ("11", "19", "22", "49", "50", "02", "10"), _PC, age_min=65),
    _s("DEPRESSION_SCREEN", "G0444", "Annual depression screening, 5-15 minutes", "preventive", 18.0, ("11", "19", "22", "49", "50"), _PC, age_min=18),
    _s("OBESITY_COUNSEL", "G0447", "Behavioral counseling for obesity, 15 minutes", "preventive", 26.0, ("11", "19", "22", "49", "50"), _PC, age_min=18),
    _s("DM_SELF_MGMT", "G0108", "Diabetes outpatient self-management training, individual, 30 minutes", "preventive", 58.0, ("11", "19", "22", "49", "50"), _PC + ("endocrinology",), units=2),
    _s("LOPS_EVAL", "G0245", "Initial physician evaluation for loss of protective sensation in diabetes", "pro", 95.0, ("11", "49"), ("podiatry", "endocrinology", "family_medicine", "internal_medicine")),
    _s("LOPS_FOLLOWUP", "G0246", "Follow-up physician evaluation for loss of protective sensation", "pro", 62.0, ("11", "49"), ("podiatry", "endocrinology", "family_medicine", "internal_medicine")),
    _s("FLU_VACCINE_ADMIN", "G0008", "Administration of influenza vaccine", "preventive", 20.0, ("11", "20", "49", "50", "19", "22"), _PC + ("pediatrics",)),
    _s("FLU_VACCINE", "Q2039", "Influenza virus vaccine, not otherwise specified", "preventive", 22.0, ("11", "20", "49", "50", "19", "22"), _PC + ("pediatrics",)),
    _s("PNEUMO_VACCINE_ADMIN", "G0009", "Administration of pneumococcal vaccine", "preventive", 20.0, ("11", "49", "50", "19", "22"), _PC, age_min=50),
    _s("COLON_SCREEN", "G0121", "Colorectal cancer screening, colonoscopy, average risk", "pro", 260.0, ("24", "22", "19", "11"), ("general_surgery", "internal_medicine", "family_medicine"), age_min=45, pa=False),
    _s("PSA_SCREEN", "G0103", "Prostate specific antigen screening", "lab", 17.0, ("81", "11", "19", "22"), ("laboratory",) + _PC + ("urology",), sex="M", age_min=40),
    _s("MAMMO_SCREEN", None, "Screening mammogram, bilateral", "imaging", 138.0, ("11", "22", "19"), ("radiology",), sex="F", age_min=40),
    _s("PAP_COLLECT", "G0101", "Cervical or vaginal cancer screening, pelvic and breast exam", "preventive", 48.0, ("11", "19", "22", "49", "50"), ("obgyn", "family_medicine", "internal_medicine"), sex="F", age_min=21),
    # --- labs (CPT-only) ---
    _s("LAB_HBA1C", None, "Hemoglobin A1c", "lab", 10.5, ("81", "11", "22", "19"), ("laboratory",) + _PC + ("endocrinology",)),
    _s("LAB_LIPID_PANEL", None, "Lipid panel", "lab", 13.0, ("81", "11", "22", "19"), ("laboratory",) + _PC),
    _s("LAB_BMP", None, "Basic metabolic panel", "lab", 8.5, ("81", "11", "22", "19"), ("laboratory",) + _PC),
    _s("LAB_CMP", None, "Comprehensive metabolic panel", "lab", 11.0, ("81", "11", "22", "19"), ("laboratory",) + _PC),
    _s("LAB_UACR", None, "Urine albumin-creatinine ratio", "lab", 7.5, ("81", "11", "22", "19"), ("laboratory",) + _PC + ("nephrology", "endocrinology")),
    _s("LAB_CBC", None, "Complete blood count", "lab", 8.0, ("81", "11", "22", "19"), ("laboratory",) + _PC + ("oncology",)),
    _s("LAB_TSH", None, "Thyroid stimulating hormone", "lab", 17.0, ("81", "11", "22", "19"), ("laboratory",) + _PC),
    _s("LAB_FLU_COVID_PCR", None, "Respiratory virus PCR panel", "lab", 55.0, ("81", "11", "20", "22"), ("laboratory",) + _PC + ("emergency_medicine",)),
    _s("LAB_STREP_RAPID", None, "Rapid strep antigen test", "lab", 16.0, ("11", "20", "81"), ("laboratory",) + _PC + ("emergency_medicine",)),
    _s("LAB_URINALYSIS", None, "Urinalysis", "lab", 4.5, ("11", "20", "81", "23"), ("laboratory",) + _PC + ("emergency_medicine",)),
    _s("LAB_PRENATAL_PANEL", None, "Prenatal laboratory panel", "lab", 75.0, ("81", "11"), ("laboratory", "obgyn"), sex="F", age_min=12, age_max=55),
    _s("LAB_GLUCOSE_TOLERANCE", None, "Glucose tolerance test, pregnancy screening", "lab", 24.0, ("81", "11"), ("laboratory", "obgyn"), sex="F", age_min=12, age_max=55),
    _s("LAB_DRUG_LEVEL", None, "Drug level monitoring", "lab", 20.0, ("81", "11"), ("laboratory",) + _PC + ("psychiatry",)),
    # --- imaging and procedures (CPT-only) ---
    _s("IMG_CHEST_XRAY", None, "Chest x-ray, two views", "imaging", 32.0, ("11", "22", "23", "21", "20"), ("radiology",)),
    _s("IMG_CT_CHEST", None, "CT chest with contrast", "imaging", 270.0, ("22", "19", "21", "23"), ("radiology",), pa=True),
    _s("IMG_CT_ABD", None, "CT abdomen and pelvis with contrast", "imaging", 310.0, ("22", "19", "21", "23"), ("radiology",), pa=True),
    _s("IMG_MRI_SPINE", None, "MRI lumbar spine", "imaging", 285.0, ("22", "19", "11"), ("radiology",), pa=True),
    _s("IMG_OB_ULTRASOUND", None, "Obstetric ultrasound", "imaging", 112.0, ("11", "22", "19"), ("radiology", "obgyn"), sex="F", age_min=12, age_max=55),
    _s("IMG_XRAY_EXTREMITY", None, "Extremity x-ray", "imaging", 36.0, ("11", "20", "22", "23"), ("radiology",)),
    _s("IMG_PET_CT", None, "PET/CT, tumour imaging", "imaging", 1100.0, ("22", "19"), ("radiology",), pa=True, age_min=18),
    _s("EYE_EXAM_DILATED", None, "Dilated retinal eye examination", "pro", 98.0, ("11", "49", "22"), ("ophthalmology", "optometry")),
    _s("BIOPSY_BREAST", None, "Breast biopsy, image guided", "pro", 480.0, ("22", "19", "24"), ("general_surgery", "radiology"), sex="F", pa=False),
    _s("BIOPSY_PROSTATE", None, "Prostate biopsy", "pro", 420.0, ("22", "24", "11"), ("urology",), sex="M"),
    _s("BIOPSY_LUNG", None, "Lung biopsy, needle", "pro", 520.0, ("22", "21"), ("pulmonology", "radiology")),
    _s("SPIROMETRY", None, "Spirometry", "pro", 38.0, ("11", "22", "19"), ("pulmonology",) + _PC),
    _s("PSYCHOTHERAPY_45", None, "Psychotherapy, 45 minutes", "behavioral", 108.0, ("11", "02", "10", "49"), ("psychiatry", "psychology")),
    _s("PSYCH_EVAL", None, "Psychiatric diagnostic evaluation", "behavioral", 165.0, ("11", "02", "10", "49"), ("psychiatry", "psychology")),
    _s("MED_MGMT_PSYCH", None, "Psychiatric medication management visit", "behavioral", 88.0, ("11", "02", "10"), ("psychiatry",)),
    _s("SUD_COUNSEL", "H0004", "Behavioral health counseling and therapy, per 15 minutes", "behavioral", 24.0, ("11", "49", "50"), ("psychology", "psychiatry"), units=3),
    _s("SUD_ASSESSMENT", "H0031", "Mental health assessment by non-physician", "behavioral", 70.0, ("11", "49", "50"), ("psychology", "psychiatry")),
    _s("IOP_SUD", "H0015", "Alcohol and/or drug services, intensive outpatient", "behavioral", 175.0, ("11", "49", "50"), ("psychiatry", "psychology")),
    _s("DIALYSIS_SESSION", None, "Outpatient hemodialysis session", "facility", 265.0, ("65",), ("dialysis_center",), pa=False),
    _s("DIALYSIS_MD_MONTH", None, "Monthly dialysis physician management", "pro", 270.0, ("65", "11"), ("nephrology",)),
    # --- surgery professional fees (CPT-only) ---
    _s("SURG_APPENDECTOMY", None, "Laparoscopic appendectomy", "pro", 810.0, ("21",), ("general_surgery",)),
    _s("SURG_CHOLECYSTECTOMY", None, "Laparoscopic cholecystectomy", "pro", 840.0, ("21", "22", "24"), ("general_surgery",)),
    _s("SURG_MASTECTOMY", None, "Mastectomy", "pro", 1250.0, ("21", "22"), ("general_surgery",), sex="F", pa=True),
    _s("SURG_PROSTATECTOMY", None, "Radical prostatectomy", "pro", 1650.0, ("21",), ("urology",), sex="M", pa=True),
    _s("SURG_COLECTOMY", None, "Partial colectomy", "pro", 1500.0, ("21",), ("general_surgery",), pa=True),
    _s("SURG_LOBECTOMY", None, "Lung lobectomy", "pro", 2100.0, ("21",), ("general_surgery",), pa=True),
    _s("DELIVERY_VAGINAL", None, "Vaginal delivery only", "pro", 1250.0, ("21",), ("obgyn",), sex="F", age_min=12, age_max=55),
    _s("DELIVERY_CESAREAN", None, "Cesarean delivery only", "pro", 1480.0, ("21",), ("obgyn",), sex="F", age_min=12, age_max=55),
    _s("RADIATION_FRACTION", None, "Radiation treatment delivery, one fraction", "pro", 310.0, ("22", "19"), ("radiation_oncology",), pa=True, age_min=18),
    _s("CHEMO_ADMIN", None, "Chemotherapy infusion administration, first hour", "pro", 135.0, ("22", "19", "11"), ("oncology",), pa=False, age_min=18),
    _s("OBS_HOURLY", "G0378", "Hospital observation service, per hour", "facility", 18.0, ("22", "23"), ("hospital",)),
    # --- facility (institutional outpatient) ---
    _s("HOSP_CLINIC_VISIT", "G0463", "Hospital outpatient clinic visit", "facility", 127.0, ("19", "22"), ("hospital",)),
    _s("ED_FACILITY_3", None, "Emergency department facility fee, moderate", "facility", 380.0, ("23",), ("hospital",)),
    _s("ED_FACILITY_4", None, "Emergency department facility fee, high", "facility", 700.0, ("23",), ("hospital",)),
    _s("ED_FACILITY_5", None, "Emergency department facility fee, critical", "facility", 1150.0, ("23",), ("hospital",)),
    # --- facility-only lines (institutional claims) ---
    _s("INPT_ROOM_BOARD", None, "Inpatient room and board, per diem", "facility", 1400.0, ("21",), ("hospital",)),
    _s("INPT_ANCILLARY", None, "Inpatient ancillary services", "facility", 1800.0, ("21",), ("hospital",)),
    _s("INPT_OPERATING_ROOM", None, "Inpatient operating room services", "facility", 3500.0, ("21",), ("hospital",)),
    _s("ASC_FACILITY", None, "Ambulatory surgery center facility fee", "facility", 480.0, ("24",), ("hospital",)),
    _s("OP_PROCEDURE_FACILITY", None, "Hospital outpatient procedure facility fee", "facility", 650.0, ("22", "19"), ("hospital",)),
    _s("OP_INFUSION_FACILITY", None, "Hospital outpatient infusion facility fee", "facility", 320.0, ("22", "19"), ("hospital",)),
    # --- ambulance, DME ---
    _s("AMBULANCE_ALS", "A0427", "Ambulance service, ALS, emergency", "transport", 480.0, ("41",), ("ambulance",)),
    _s("AMBULANCE_BLS", "A0429", "Ambulance service, BLS, emergency", "transport", 400.0, ("41",), ("ambulance",)),
    _s("AMBULANCE_MILEAGE", "A0425", "Ground mileage, per statute mile", "transport", 8.2, ("41",), ("ambulance",), units=8),
    _s("DME_GLUCOSE_STRIPS", "A4253", "Blood glucose test strips, per 50", "dme", 28.0, ("12", "11"), ("dme_supplier",), units=2),
    _s("DME_LANCETS", "A4259", "Lancets, per box of 100", "dme", 8.0, ("12", "11"), ("dme_supplier",)),
    _s("DME_GLUCOSE_MONITOR", "E0607", "Home blood glucose monitor", "dme", 32.0, ("12", "11"), ("dme_supplier",)),
    _s("DME_CPAP", "E0601", "Continuous positive airway pressure device", "dme", 690.0, ("12",), ("dme_supplier",), pa=True, age_min=18),
    # --- drugs billed on medical claims (J codes) ---
    _s("J_CEFTRIAXONE", "J0696", "Injection, ceftriaxone sodium, per 250 mg", "drug", 6.0, ("23", "20", "11", "21"), ("emergency_medicine",) + _PC + ("hospitalist",), units=4),
    _s("J_KETOROLAC", "J1885", "Injection, ketorolac tromethamine, per 15 mg", "drug", 2.4, ("23", "20", "11"), ("emergency_medicine",) + _PC, units=2),
    _s("J_DEXAMETHASONE", "J1100", "Injection, dexamethasone sodium phosphate, 1 mg", "drug", 0.9, ("23", "20", "11", "21"), ("emergency_medicine",) + _PC, units=10),
    _s("J_PEMBROLIZUMAB", "J9271", "Injection, pembrolizumab, 1 mg", "drug", 55.0, ("22", "19", "11"), ("oncology",), units=200, pa=True, age_min=18),
    _s("J_CARBOPLATIN", "J9045", "Injection, carboplatin, 50 mg", "drug", 2.2, ("22", "19", "11"), ("oncology",), units=9, pa=False, age_min=18),
    _s("J_PEMETREXED", "J9305", "Injection, pemetrexed, 10 mg", "drug", 3.4, ("22", "19", "11"), ("oncology",), units=50, pa=True, age_min=18),
    _s("J_TRASTUZUMAB", "J9355", "Injection, trastuzumab, 10 mg", "drug", 92.0, ("22", "19", "11"), ("oncology",), units=40, pa=True, sex="F", age_min=18),
    _s("J_PACLITAXEL", "J9267", "Injection, paclitaxel, 1 mg", "drug", 0.22, ("22", "19", "11"), ("oncology",), units=300, age_min=18),
    _s("J_OXALIPLATIN", "J9263", "Injection, oxaliplatin, 0.5 mg", "drug", 0.2, ("22", "19", "11"), ("oncology",), units=300, age_min=18),
    _s("J_FLUOROURACIL", "J9190", "Injection, fluorouracil, 500 mg", "drug", 1.6, ("22", "19", "11"), ("oncology",), units=8, age_min=18),
    _s("J_LEUPROLIDE", "J1950", "Injection, leuprolide acetate (for depot suspension), per 3.75 mg", "drug", 290.0, ("11", "22", "19"), ("urology", "oncology"), units=8, sex="M", age_min=18),
    _s("J_PEGFILGRASTIM", "J2505", "Injection, pegfilgrastim, 6 mg", "drug", 3800.0, ("11", "22", "19"), ("oncology",), age_min=18, pa=True),
)

SERVICES: dict[str, Service] = {s.key: s for s in _ITEMS}

SYSTEM_SVC = "SHAPE-SVC"
SYSTEM_HCPCS = "HCPCS"
SYSTEM_CPT = "CPT"


def code_for(service: Service, cpt: dict[str, str] | None = None) -> tuple[str, str]:
    """(code system, code) a line of ``service`` carries: HCPCS, the BYO CPT code, or the key."""
    if service.hcpcs:
        return SYSTEM_HCPCS, service.hcpcs
    if cpt and service.key in cpt:
        return SYSTEM_CPT, cpt[service.key]
    return SYSTEM_SVC, service.key
