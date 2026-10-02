# ruff: noqa: E501
"""Tiny hand-made inputs in each source's real layout (invented text, so no licence applies).

Every builder is tested against these: the network is never used.
"""

from __future__ import annotations

import io
import zipfile


def order_text(rows: list[tuple[str, int, str, str]]) -> str:
    """A CDC/CMS order file: order(5) code(7) flag(1) short(60) long."""
    return "\r\n".join(
        f"{i:05d} {code:<7} {flag} {short:<60} {long}"
        for i, (code, flag, short, long) in enumerate(rows, 1)
    )


def zip_bytes(files: dict[str, str | bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


# ICD-10-CM: three releases (Oct 2019, Oct 2020, Oct 2021) with a code added, a header that
# becomes billable, a code retired, and a 7th-character stem.
CM_R1 = [
    ("A00", 0, "Cholera", "Cholera"),
    ("A009", 1, "Cholera, unspecified", "Cholera, unspecified"),
    ("E11", 0, "Type 2 diabetes mellitus", "Type 2 diabetes mellitus"),
    ("E119", 1, "Type 2 DM w/o complications", "Type 2 diabetes mellitus without complications"),
    ("M545", 1, "Low back pain", "Low back pain"),
    (
        "S72001A",
        1,
        "Fx unsp part of neck of right femur",
        "Fracture of unspecified part of neck of right femur, initial encounter for closed fracture",
    ),
    (
        "C441021",
        1,
        "Skin ca right upper eyelid",
        "Unspecified malignant neoplasm of skin of right upper eyelid, including canthus",
    ),
]
CM_R2 = [r for r in CM_R1 if r[0] != "M545"] + [
    ("M545", 0, "Low back pain", "Low back pain"),
    ("M5450", 1, "Low back pain, unspecified", "Low back pain, unspecified"),
    ("U071", 1, "COVID-19", "COVID-19"),
]
CM_R3 = [r for r in CM_R2 if r[0] != "A009"] + [
    ("A0090", 1, "Cholera, unspecified", "Cholera, unspecified"),
]
CM_TABULAR = """<?xml version="1.0" encoding="utf-8"?>
<ICD10CM.tabular>
  <version>2022</version>
  <chapter>
    <name>1</name>
    <desc>Certain infectious and parasitic diseases (A00-B99)</desc>
    <section id="A00-A09">
      <desc>Intestinal infectious diseases (A00-A09)</desc>
      <diag><name>A00</name><desc>Cholera</desc>
        <diag><name>A00.9</name><desc>Cholera, unspecified</desc></diag>
      </diag>
    </section>
  </chapter>
  <chapter>
    <name>4</name>
    <desc>Endocrine, nutritional and metabolic diseases (E00-E89)</desc>
    <section id="E08-E13">
      <desc>Diabetes mellitus (E08-E13)</desc>
      <diag><name>E11</name><desc>Type 2 diabetes mellitus</desc>
        <diag><name>E11.9</name><desc>Type 2 diabetes mellitus without complications</desc></diag>
      </diag>
    </section>
  </chapter>
  <chapter>
    <name>13</name>
    <desc>Diseases of the musculoskeletal system and connective tissue (M00-M99)</desc>
    <section id="M50-M54">
      <desc>Other dorsopathies (M50-M54)</desc>
      <diag><name>M54</name><desc>Dorsalgia</desc></diag>
    </section>
  </chapter>
  <chapter>
    <name>19</name>
    <desc>Injury, poisoning and certain other consequences of external causes (S00-T88)</desc>
    <section id="S70-S79">
      <desc>Injuries to the hip and thigh (S70-S79)</desc>
      <diag><name>S72</name><desc>Fracture of femur</desc>
        <sevenChrNote><note>7th characters</note></sevenChrNote>
        <sevenChrDef>
          <extension char="A">initial encounter for closed fracture</extension>
          <extension char="D">subsequent encounter for closed fracture with routine healing</extension>
        </sevenChrDef>
        <diag><name>S72.0</name><desc>Fracture of head and neck of femur</desc>
          <diag><name>S72.00</name><desc>Fracture of unspecified part of neck of femur</desc>
            <diag><name>S72.001</name><desc>Fracture of unspecified part of neck of right femur</desc></diag>
          </diag>
        </diag>
      </diag>
    </section>
  </chapter>
</ICD10CM.tabular>
"""

# ICD-10-PCS: same layout as CM's order file.
PCS_R1 = [
    (
        "0FT44ZZ",
        1,
        "Resection of Gallbladder, Perc Endo Approach",
        "Resection of Gallbladder, Percutaneous Endoscopic Approach",
    ),
    (
        "0FT40ZZ",
        1,
        "Resection of Gallbladder, Open Approach",
        "Resection of Gallbladder, Open Approach",
    ),
]
PCS_R2 = PCS_R1 + [
    (
        "0FT47ZZ",
        1,
        "Resection of Gallbladder, Via Natural Opening",
        "Resection of Gallbladder, Via Natural or Artificial Opening",
    ),
]


def hcpcs_line(code: str, rid: str, long: str, short: str = "", **f: str) -> str:
    """One record of the 320-column HCPCS layout (CMS recordlayout, positions 1-based)."""
    buf = [" "] * 320

    def put(start: int, text: str, width: int) -> None:
        for i, ch in enumerate(text.ljust(width)[:width]):
            buf[start - 1 + i] = ch

    if rid in "78":
        put(4, code, 2)
    else:
        put(1, code, 5)
    put(6, f.get("seq", "00100"), 5)
    put(11, rid, 1)
    put(12, long, 80)
    put(92, short, 28)
    put(120, f.get("pricing", "  "), 2)
    put(230, f.get("coverage", " "), 1)
    put(257, f.get("betos", "   "), 3)
    put(269, f.get("added", ""), 8)
    put(277, f.get("effective", ""), 8)
    put(285, f.get("end", ""), 8)
    put(293, f.get("action", "N"), 1)
    return "".join(buf).rstrip()


HCPCS_TEXT = "\n".join(
    [
        hcpcs_line(
            "J9999",
            "3",
            "Injection, invented drug alfa, 1 mg",
            "Inj invented alfa",
            added="20100101",
            effective="20100101",
            betos="O1E",
            pricing="51",
            coverage="C",
        ),
        hcpcs_line(
            "A4000",
            "3",
            "A very long invented supply description that runs past the first line of the",
            "Invented supply",
            added="20000101",
            effective="20000101",
            end="20240101",
        ),
        hcpcs_line("A4000", "4", "record and continues here", seq="00200"),
        hcpcs_line(
            "ZZ",
            "7",
            "Invented modifier, level two",
            "Invented mod",
            added="20050101",
            effective="20050101",
        ),
        hcpcs_line("ZZ", "8", "second line of the modifier", seq="00200"),
    ]
)
HCPCS_CPT_LINE = hcpcs_line(
    "99213", "3", "Invented CPT-looking record", "Invented", added="20000101"
)

NDC_PRODUCT = (
    "PRODUCTID\tPRODUCTNDC\tPRODUCTTYPENAME\tPROPRIETARYNAME\tPROPRIETARYNAMESUFFIX\t"
    "NONPROPRIETARYNAME\tDOSAGEFORMNAME\tROUTENAME\tSTARTMARKETINGDATE\tENDMARKETINGDATE\t"
    "MARKETINGCATEGORYNAME\tAPPLICATIONNUMBER\tLABELERNAME\tSUBSTANCENAME\t"
    "ACTIVE_NUMERATOR_STRENGTH\tACTIVE_INGRED_UNIT\tPHARM_CLASSES\tDEASCHEDULE\t"
    "NDC_EXCLUDE_FLAG\tLISTING_RECORD_CERTIFIED_THROUGH\n"
    "0002-0152_a1\t0002-0152\tHUMAN PRESCRIPTION DRUG\tInventa\t\tinventamab\tINJECTION\tSUBCUTANEOUS\t20240328\t\tNDA\tNDA000001\tAcme Pharma\tINVENTAMAB\t2.5\tmg/.5mL\tClass A [EPC]\t\tN\t20271231\n"
    "1234-5678_b2\t1234-5678\tHUMAN PRESCRIPTION DRUG\t\t\tfakeprilate\tTABLET\tORAL\t20100101\t20231231\tANDA\tANDA000002\tBeta Labs\tFAKEPRILATE\t10\tmg/1\t\tCII\tN\t\n"
    "12345-678_c3\t12345-678\tHUMAN OTC DRUG\tGammaCare\t\tgammacillin\tCAPSULE\tORAL\t20150601\t\tOTC\t\tGamma Inc\tGAMMACILLIN\t250\tmg/1\t\t\tN\t20261231\n"
)
NDC_PACKAGE = (
    "PRODUCTID\tPRODUCTNDC\tNDCPACKAGECODE\tPACKAGEDESCRIPTION\tSTARTMARKETINGDATE\tENDMARKETINGDATE\tNDC_EXCLUDE_FLAG\tSAMPLE_PACKAGE\n"
    "0002-0152_a1\t0002-0152\t0002-0152-01\t1 VIAL in 1 CARTON\t20240328\t\tN\tN\n"
    "0002-0152_a1\t0002-0152\t0002-0152-61\t4 VIAL in 1 CARTON\t20250501\t\tN\tY\n"
    "1234-5678_b2\t1234-5678\t1234-5678-90\t100 TABLET in 1 BOTTLE\t20100101\t20231231\tN\tN\n"
    "12345-678_c3\t12345-678\t12345-678-90\t90 CAPSULE in 1 BOTTLE\t20150601\t\tN\tN\n"
)

RXN_CONSO = (
    "617310|ENG|P|L1|PF|S1|Y|A1||617310||RXNORM|SCD|617310|atorvastatin 20 MG Oral Tablet||N|4096|\n"
    "83367|ENG|P|L2|PF|S2|Y|A2||83367||RXNORM|IN|83367|atorvastatin||N|4096|\n"
    "153165|ENG|P|L3|PF|S3|Y|A3||153165||RXNORM|BN|153165|Lipitor||N|4096|\n"
    "999|ENG|P|L4|PF|S4|Y|A4||999||RXNORM|SY|999|synonym text||N|4096|\n"
    "55|ENG|P|L5|PF|S5|Y|A5||55||MTHSPL|SU|NR7O1405Q9|other source||N|4096|\n"
    "88|ENG|P|L6|PF|S6|Y|A6||88||RXNORM|SCD|88|obsolete thing||O|4096|\n"
)
RXN_SAT = (
    "617310|||A9|AUI|617310|T1||NDC|RXNORM|00071015523|N|4096|\n"
    "617310|||A9|AUI|617310|T2||NDC|MTHSPL|0071-0155-23|N|4096|\n"
    "617310|||A9|AUI|617310|T3||RXN_HUMAN_DRUG|RXNORM|Yes|N|4096|\n"
    "83367|||A9|AUI|83367|T4||NDC|RXNORM|short|N|4096|\n"
)

POS_HTML = """<table><tr><th>Code(s)</th><th>Name</th><th>Description</th></tr>
<tr><td>01</td><td>Pharmacy</td><td>A facility where drugs are dispensed. (Effective October 1, 2003) (Revised, effective October 1, 2005)</td></tr>
<tr><td>11</td><td>Office</td><td>Location, other than a hospital, where a professional provides care.</td></tr>
<tr><td>02</td><td>Telehealth Provided Other than in Patient&rsquo;s Home</td><td>Via telecommunication. (Effective January 1, 2017)</td></tr>
<tr><td>35-40</td><td>Unassigned</td><td>N/A</td></tr>
</table>"""

HCC_CSV = (
    '"ICD-10-CM Codes, ESRD, CMS-HCC and RxHCC Models",,,,,,\n'
    ",,,,,,\n"
    '"Diagnosis\nCode",Description,"CMS-HCC\nESRD\nModel\nCategory\nV24","CMS-HCC\nModel\nCategory\nV28","RxHCC\nModel\nCategory\nV08","CMS-HCC\nModel\nCategory\nV28 for 2026\nPayment Year","RxHCC\nModel\nCategory\nV08 for 2026\nPayment Year"\n'
    "E1165,Type 2 DM with hyperglycemia,38,37,30,Yes,No\n"
    "I10,Essential hypertension,,,187,No,Yes\n"
    "A0104,Typhoid arthritis,39,92,,Yes,No\n"
    ",,,,,,\n"
    "Source: invented,,,,,,\n"
)

CCSR_CSV = (
    "'ICD-10-CM CODE','ICD-10-CM CODE DESCRIPTION','Default CCSR CATEGORY IP','Default CCSR CATEGORY DESCRIPTION IP',"
    "'Default CCSR CATEGORY OP','Default CCSR CATEGORY DESCRIPTION OP','CCSR CATEGORY 1','CCSR CATEGORY 1 DESCRIPTION',"
    "'CCSR CATEGORY 2','CCSR CATEGORY 2 DESCRIPTION','CCSR CATEGORY 3','CCSR CATEGORY 3 DESCRIPTION',"
    "'CCSR CATEGORY 4','CCSR CATEGORY 4 DESCRIPTION','CCSR CATEGORY 5','CCSR CATEGORY 5 DESCRIPTION',"
    "'CCSR CATEGORY 6','CCSR CATEGORY 6 DESCRIPTION','Rationale for Default Assignment'\n"
    "'A009',\"Cholera, unspecified\",'DIG001',Intestinal infection,'DIG001',Intestinal infection,'DIG001',Intestinal infection,"
    "'INF003',Bacterial infections,' ',,' ',,' ',,' ',,06 Infectious conditions\n"
)

MCE_AGE = """Definitions of Medicare Code Edits
4. Age conflict
A. Perinatal/Newborn diagnoses\t9
B. Pediatric diagnoses (age 0 through 17)\t10
4. Age conflict
Some prose that mentions codes like A33 in a sentence.
A. Perinatal/Newborn diagnoses
A33\tTetanus neonatorum
Z00110\tHealth examination for newborn under 8 days old
B. Pediatric diagnoses (age 0 through 17)
E8411\tMeconium ileus
C. Maternity diagnoses (age 9 through 64)
O80\tEncounter for full-term uncomplicated delivery
D. Adult diagnoses (age 15 through 124)
N401\tBenign prostatic hyperplasia with lower urinary tract symptoms
5. Sex conflict (deactivated as of 10/01/2024)
Prose only.
"""
MCE_SEX = """Definitions of Medicare Code Edits
5. Sex conflict
A. Diagnoses for females only\t94
5. Sex conflict
A. Diagnoses for females only
O80\tEncounter for full-term uncomplicated delivery
N736\tFemale pelvic peritoneal adhesions
B. Procedures for females only
0UT90ZZ\tResection of Uterus, Open Approach
C. Diagnoses for males only
N401\tBenign prostatic hyperplasia
C61\tMalignant neoplasm of prostate
D. Procedures for males only
0VT08ZZ\tResection of Prostate, Via Natural or Artificial Opening Endoscopic
6. Manifestation code as principal diagnosis
D630\tAnemia in neoplastic disease
"""
