"""The problem list as ICD-10-CM concepts: what each condition is coded as on a claim."""

from __future__ import annotations

from datetime import date

from .model import Person


def dm_codes(person: Person) -> list[str]:
    c = person.conds["dm"]
    t1 = c.data["type"] == "E10"
    out: list[str] = []
    if not t1 and person.has("ckd"):
        out.append("E11.22")
    comp = c.data.get("complications", [])
    if not t1:
        for key, code in (("neuropathy", "E11.42"), ("retinopathy", "E11.319"),
                          ("angiopathy", "E11.51"), ("ulcer", "E11.621")):
            if key in comp:
                out.append(code)
    if c.data.get("hyperglycemia"):
        out.append("E10.65" if t1 else "E11.65")
    if not out:
        out.append("E10.9" if t1 else "E11.9")
    return out


def dm_status_codes(person: Person) -> list[str]:
    insulin = any(t.drug_key.startswith("insulin") and t.stop is None for t in person.therapies)
    if insulin:
        return ["Z79.4"]
    if person.conds["dm"].data["type"] == "E11" and any(
        t.drug_key in ("metformin", "metformin_er", "glipizide", "glimepiride", "sitagliptin")
        and t.stop is None for t in person.therapies
    ):
        return ["Z79.84"]
    return []


def ckd_codes(person: Person) -> list[str]:
    c = person.conds["ckd"]
    stage = c.stage
    if stage == 3:
        return ["N18.31" if c.data.get("sub") == "a" else "N18.32" if c.data.get("sub") == "b" else "ckd3"]
    return {1: ["N18.1"], 2: ["N18.2"], 4: ["N18.4"], 5: ["N18.5"], 6: ["N18.6", "Z99.2"]}[stage]


def htn_code(person: Person) -> str:
    if person.has("ckd"):
        return "I12.0" if person.conds["ckd"].stage >= 5 else "I12.9"
    if person.has("hf"):
        return "I11.0"
    return "I10"


_OBESITY_Z = {"obesity_class1": "Z68.30", "obesity_class2": "Z68.35", "obesity_class3": "Z68.41"}


def problem_codes(person: Person, day: date) -> list[str]:
    """Every coded condition, most clinically weighty first."""
    out: list[str] = []
    for key in ("cancer", "esrd"):
        if key in person.flags:
            out.extend(person.flags[key])
    if person.has("dm"):
        out += dm_codes(person) + dm_status_codes(person)
    if person.has("hf"):
        out.append("I50.22" if person.conds["hf"].data.get("systolic") else "I50.9")
    if person.has("ckd"):
        out += ckd_codes(person)
    if person.has("htn"):
        out.append(htn_code(person))
    if person.has("cad"):
        out.append("I25.10")
    if person.has("afib"):
        out.append("I48.91")
    if person.has("lipid"):
        out.append(person.conds["lipid"].code)
    if person.has("copd"):
        out.append("J44.9")
        out.append("F17.210" if person.conds["copd"].data.get("smoker") else "Z87.891")
    if person.has("asthma"):
        out.append(person.conds["asthma"].code)
    if person.has("obesity") and person.conds["obesity"].data.get("coded"):
        cls = person.conds["obesity"].data["cls"]
        out.append(cls)
        if person.age(day) >= 20 and person.conds["obesity"].data.get("z68"):
            out.append(_OBESITY_Z[cls])
    for key, concept in (
        ("depression", None), ("anxiety", None), ("adhd", None), ("sud_opioid", "F11.20"),
        ("sud_alcohol", "F10.20"), ("bipolar", "F31.9"), ("schizophrenia", "F20.9"),
        ("hypothyroid", "E03.9"), ("gerd", "K21.9"), ("osteoporosis", "M81.0"), ("bph", "N40.1"),
        ("autoimmune", None),
    ):
        if person.has(key):
            out.append(concept or person.conds[key].code)
    return out


# the diagnosis prefixes (ICD-10-CM) that support a drug indication: the coherence rule the
# acceptance tests check for every order and fill
INDICATION_DX: dict[str, tuple[str, ...]] = {
    "dm1": ("E10", "Z79.4"), "dm2": ("E11", "R73.03"), "dm2_statin": ("E11",), "gdm": ("O24",),
    "htn": ("I10", "I11", "I12", "I13"), "hf": ("I50", "I11"), "cad": ("I25",), "afib": ("I48",),
    "dm_ckd": ("E11", "N18", "I12"), "ckd": ("N18", "I12", "E11"), "lipid": ("E78",),
    "asthma": ("J45",), "copd": ("J44",), "asthma_exac": ("J45",), "copd_exac": ("J44",),
    "depression": ("F32", "F33"), "anxiety": ("F41",), "adhd": ("F90",), "sud_opioid": ("F11",),
    "bipolar": ("F31",), "schizophrenia": ("F20",), "cancer_breast": ("C50", "Z85.3"),
    "cancer_prostate": ("C61", "Z85.46"), "cancer_colon": ("C18", "Z85.038"), "nausea_chemo": ("C",),
    "nausea_pregnancy": ("Z34", "O"), "pregnancy": ("Z34", "O09", "Z3A"), "pregnancy_htn": ("O13", "O14"),
    "hypothyroid": ("E03",), "gerd": ("K21",), "osteoporosis": ("M81",), "bph": ("N40",),
    "uri_bacterial": ("J06", "J20", "J01", "H66", "J18"), "strep": ("J02",), "pneumonia": ("J18", "J12"),
    "flu": ("J10", "J11"), "cough": ("R05",), "uri_viral": ("J06", "J20"), "uti": ("N39",),
    "skin_infection": ("L03",), "gastroenteritis": ("A08",), "insomnia": ("G47",),
    "pain_acute": ("M", "S", "R", "G89"),
    "autoimmune": ("M06", "L40", "K50", "K51"),
}
