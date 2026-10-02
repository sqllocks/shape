"""Chronic-care pathways written as behavior-engine module documents (``shape-behavior/1``).

Diabetes, hypertension and lipid disorders, each as small state machines: *onset* (who has the
condition on the first day, and who develops it, with age-band hazards), *reviews* (the visit cycle
and the labs that go with it), *annual exams*, and *meds* (the regimens, which read the member's
other active conditions).  The documents are built from the calibration table, so every probability
and mean in them is a calibrated rate and a captured shape can replace it.

The modules emit clinical facts and timing only: ``condition_onset`` with an ICD-10-CM code,
``medication_order`` and ``medication_end`` with a drug key, and ``visit`` / ``exam`` domain events
whose payload names the setting, specialty, diagnoses and services.  Turning them into claims is the
job of ``behavior_engine.py`` (the same claims and pharmacy pipeline the native modules feed).

The starting profile of each member (age band, obesity, who has diabetes, hypertension or a lipid
disorder on day one) is written into the entity attributes by ``behavior_engine``, from the same
clustered cohort the native engine uses, so the comorbidity clustering is shared.
"""

from __future__ import annotations

from typing import Any

from .calibration import BANDS, Calibration

FORMAT = "shape-behavior/1"
MID_AGE = {"0-17": 9, "18-34": 26, "35-44": 40, "45-54": 50, "55-64": 60, "65-74": 70, "75+": 80}
ICD = "ICD-10-CM"
DRUG = "shape-drug"
LIPID_CODES = ("E78.5", "E78.2", "E78.00")


def _attr(name: str, op: str, value: Any) -> dict[str, Any]:
    return {"type": "attribute", "attribute": name, "op": op, "value": value}


def _active(*codes: str) -> dict[str, Any]:
    conds = [{"type": "active_condition", "codes": [{"system": ICD, "code": c}]} for c in codes]
    return conds[0] if len(conds) == 1 else {"type": "or", "conditions": conds}


def _days(mean: float) -> dict[str, Any]:
    return {"kind": "exponential", "mean": round(max(mean, 1.0), 3), "unit": "days"}


def _uniform_days(low: float, high: float) -> dict[str, Any]:
    return {"kind": "uniform", "low": low, "high": high, "unit": "days"}


def _chain(p: float, yes: str, no: str) -> dict[str, Any]:
    return {"distributed": [{"p": round(p, 6), "to": yes}, {"p": round(1 - p, 6), "to": no}]}


def _onset(code: str) -> dict[str, Any]:
    return {"system": ICD, "code": code}


def _doc(name: str, states: dict[str, Any], remarks: str) -> dict[str, Any]:
    return {
        "format": FORMAT,
        "name": name,
        "initial": "start",
        "remarks": [remarks],
        "states": states,
    }


def _guard(condition: dict[str, Any], to: str) -> dict[str, Any]:
    return {"type": "guard", "condition": condition, "poll": "7 days", "transition": {"direct": to}}


def _visit(
    module: str,
    reason: str,
    specialty: str,
    dx: list[str],
    labs: list[str] | None = None,
    to: str = "gap",
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"module": module, "reason": reason, "specialty": specialty, "dx": dx}
    if labs:
        payload["labs"] = labs
    payload.update(extra)
    return {"type": "event", "event": "visit", "payload": payload, "transition": {"direct": to}}


def _exam(
    module: str, reason: str, specialty: str, service: str, dx: list[str], to: str
) -> dict[str, Any]:
    return {
        "type": "event",
        "event": "exam",
        "payload": {
            "module": module,
            "reason": reason,
            "specialty": specialty,
            "service": service,
            "dx": dx,
        },
        "transition": {"direct": to},
    }


def _order(drug: str, indication: str, to: str) -> dict[str, Any]:
    return {
        "type": "medication_order",
        "codes": [{"system": DRUG, "code": drug, "display": indication}],
        "transition": {"direct": to},
    }


def _incident_states(
    key: str, hazards: dict[str, float], obese_multiple: float, codes: dict[str, str]
) -> dict[str, Any]:
    """Age-band delay states: the time to onset is exponential with the band's hazard (and a
    higher one for obese members); ``codes`` maps a band to the code that onset carries."""
    states: dict[str, Any] = {}
    routes: list[dict[str, Any]] = []
    for band in BANDS:
        h = hazards.get(band, 0.0)
        if h <= 0:
            continue
        for obese, mult in ((1, obese_multiple), (0, 1.0)):
            name = f"inc_{band}_{'ob' if obese else 'no'}"
            cond = {
                "type": "and",
                "conditions": [_attr("age_band", "==", band), _attr("obese", "==", obese)],
            }
            routes.append({"if": cond, "to": name})
            states[name] = {
                "type": "delay",
                "delay": _days(365.25 / (h * mult)),
                "transition": {"direct": f"onset_{band}"},
            }
        states[f"onset_{band}"] = {
            "type": "condition_onset",
            "codes": [_onset(codes[band])],
            "transition": {"direct": "end"},
        }
    routes.append({"to": "end"})
    states["incident"] = {"type": "simple", "transition": {"conditional": routes}}
    return states


def build_modules(cal: Calibration | None = None) -> list[dict[str, Any]]:
    """The module documents, with every rate read from ``cal`` (default: the public table)."""
    from types import SimpleNamespace

    from .cohort import Prevalence

    cal = cal or Calibration()
    prev = Prevalence(SimpleNamespace(cal=cal))  # type: ignore[arg-type]
    hazard = {
        k: {b: prev.incidence(k, MID_AGE[b], "F") for b in BANDS} for k in ("dm", "htn", "lipid")
    }
    docs: list[dict[str, Any]] = []

    # ---- diabetes ---------------------------------------------------------------------------
    dm_codes = {b: ("E10.9" if b == "0-17" else "E11.9") for b in BANDS}
    onset = {
        "start": {"type": "initial", "transition": {"direct": "route"}},
        "route": {
            "type": "simple",
            "transition": {
                "conditional": [
                    {"if": _attr("dm_start", "==", "t1"), "to": "onset_t1"},
                    {"if": _attr("dm_start", "==", "t2"), "to": "onset_t2"},
                    {"to": "incident"},
                ]
            },
        },
        "onset_t1": {
            "type": "condition_onset",
            "codes": [_onset("E10.9")],
            "transition": {"direct": "end"},
        },
        "onset_t2": {
            "type": "condition_onset",
            "codes": [_onset("E11.9")],
            "transition": {"direct": "end"},
        },
        "end": {"type": "terminal"},
        **_incident_states("dm", hazard["dm"], 2.5, dm_codes),
    }
    docs.append(_doc("payer_dm_onset", onset, "Who has diabetes on day one and who develops it."))

    t1_share_spec = 0.70
    spec_share = 0.12
    visits_per_year = float(cal.get("dm.visits_per_year"))
    gap = _days(365.25 / visits_per_year)
    a1c_p = 0.8
    dm_active = _active("E11.9", "E10.9")
    reviews = {
        "start": {"type": "initial", "transition": {"direct": "wait"}},
        "wait": _guard(dm_active, "first"),
        "first": {
            "type": "delay",
            "delay": _uniform_days(0, 150),
            "transition": {"direct": "pick"},
        },
        "pick": {
            "type": "simple",
            "transition": {
                "conditional": [
                    {"if": _active("E10.9"), "to": "pick_t1"},
                    {"to": "pick_t2"},
                ]
            },
        },
        "pick_t1": {"type": "simple", "transition": _chain(t1_share_spec, "lab_spec", "lab_pcp")},
        "pick_t2": {"type": "simple", "transition": _chain(spec_share, "lab_spec", "lab_pcp")},
        "lab_spec": {"type": "simple", "transition": _chain(a1c_p, "visit_spec_a1c", "visit_spec")},
        "lab_pcp": {"type": "simple", "transition": _chain(a1c_p, "visit_pcp_a1c", "visit_pcp")},
        "visit_spec": _visit("diabetes", "diabetes review", "endocrinology", ["dm"]),
        "visit_spec_a1c": _visit(
            "diabetes", "diabetes review", "endocrinology", ["dm"], ["LAB_HBA1C"]
        ),
        "visit_pcp": _visit("diabetes", "diabetes review", "pcp", ["dm"]),
        "visit_pcp_a1c": _visit("diabetes", "diabetes review", "pcp", ["dm"], ["LAB_HBA1C"]),
        "gap": {"type": "delay", "delay": gap, "transition": {"direct": "pick"}},
    }
    docs.append(
        _doc("payer_dm_reviews", reviews, "Diabetes review visits with HbA1c at most reviews.")
    )

    for name, rate_name, reason, specialty, service in (
        (
            "payer_dm_eye",
            "dm.eye_exam_annual",
            "dilated retinal eye exam",
            "eye",
            "EYE_EXAM_DILATED",
        ),
        ("payer_dm_foot", "dm.foot_exam_annual", "diabetic foot exam", "podiatry", "LOPS_FOLLOWUP"),
        ("payer_dm_uacr", "dm.uacr_annual", "urine albumin-creatinine ratio", "lab", "LAB_UACR"),
    ):
        annual = float(cal.get(rate_name))
        per_year = -__import__("math").log(1 - annual)  # so that P(at least one in a year) = annual
        docs.append(
            _doc(
                name,
                {
                    "start": {"type": "initial", "transition": {"direct": "wait"}},
                    "wait": _guard(dm_active, "gap"),
                    "gap": {
                        "type": "delay",
                        "delay": _days(365.25 / per_year),
                        "transition": {"direct": "exam"},
                    },
                    "exam": _exam("diabetes", reason, specialty, service, ["dm"], "gap"),
                },
                f"{reason}: a Poisson process whose yearly chance of at least one is {annual:g}.",
            )
        )

    t2_on_insulin = float(cal.get("dm.type2_on_insulin"))
    metformin = float(cal.get("dm.metformin_share"))
    newer = float(cal.get("dm.newer_agent_share"))
    statin = float(cal.get("dm.statin_age_40_75"))
    meds = {
        "start": {"type": "initial", "transition": {"direct": "wait"}},
        "wait": _guard(dm_active, "regimen"),
        "regimen": {
            "type": "delay",
            "delay": _uniform_days(0, 10),
            "transition": {"direct": "branch"},
        },
        "branch": {
            "type": "simple",
            "transition": {
                "conditional": [{"if": _active("E10.9"), "to": "t1_basal"}, {"to": "met_pick"}]
            },
        },
        "t1_basal": _order("insulin_glargine", "dm1", "t1_bolus"),
        "t1_bolus": _order("insulin_lispro", "dm1", "statin_route"),
        "met_pick": {"type": "simple", "transition": _chain(metformin, "met", "agent_pick")},
        "met": _order("metformin", "dm2", "agent_pick"),
        "agent_pick": {
            "type": "simple",
            "transition": _chain(newer, "agent_which", "insulin_pick"),
        },
        "agent_which": {
            "type": "simple",
            "transition": {
                "distributed": [
                    {"p": 0.3, "to": "agent_sglt2"},
                    {"p": 0.4, "to": "agent_glp1"},
                    {"p": 0.3, "to": "agent_dpp4"},
                ]
            },
        },
        "agent_sglt2": _order("empagliflozin", "dm2", "insulin_pick"),
        "agent_glp1": _order("dulaglutide", "dm2", "insulin_pick"),
        "agent_dpp4": _order("sitagliptin", "dm2", "insulin_pick"),
        "insulin_pick": {
            "type": "simple",
            "transition": _chain(t2_on_insulin, "insulin", "statin_route"),
        },
        "insulin": _order("insulin_glargine", "dm2", "statin_route"),
        "statin_route": {
            "type": "simple",
            "transition": {
                "conditional": [
                    {"if": _active(*LIPID_CODES), "to": "end"},
                    {
                        "if": {
                            "type": "or",
                            "conditions": [
                                _attr("age_band", "==", b) for b in ("45-54", "55-64", "65-74")
                            ],
                        },
                        "to": "statin_pick",
                    },
                    {"if": _attr("age_band", "==", "35-44"), "to": "statin_pick_young"},
                    {"to": "end"},
                ]
            },
        },
        "statin_pick": {"type": "simple", "transition": _chain(statin, "statin", "end")},
        "statin_pick_young": {
            "type": "simple",
            "transition": _chain(statin * 0.3, "statin", "end"),
        },
        "statin": _order("atorvastatin", "dm2_statin", "end"),
        "end": {"type": "terminal"},
    }
    docs.append(_doc("payer_dm_meds", meds, "Starting regimen of a member with diabetes."))

    # ---- hypertension ---------------------------------------------------------------------------
    htn_onset = {
        "start": {"type": "initial", "transition": {"direct": "route"}},
        "route": {
            "type": "simple",
            "transition": {
                "conditional": [
                    {"if": _attr("htn_start", "==", 1), "to": "onset"},
                    {"to": "incident"},
                ]
            },
        },
        "onset": {
            "type": "condition_onset",
            "codes": [_onset("I10")],
            "transition": {"direct": "end"},
        },
        "end": {"type": "terminal"},
        **_incident_states("htn", hazard["htn"], 1.6, {b: "I10" for b in BANDS}),
    }
    docs.append(
        _doc("payer_htn_onset", htn_onset, "Who has hypertension on day one and who develops it.")
    )
    htn_visits = float(cal.get("htn.visits_per_year"))
    docs.append(
        _doc(
            "payer_htn_reviews",
            {
                "start": {"type": "initial", "transition": {"direct": "wait"}},
                "wait": _guard(_active("I10"), "first"),
                "first": {
                    "type": "delay",
                    "delay": _uniform_days(0, 150),
                    "transition": {"direct": "pick"},
                },
                "pick": {"type": "simple", "transition": _chain(0.08, "cardio", "pcp")},
                "cardio": {
                    "type": "simple",
                    "transition": _chain(0.5, "visit_cardio_bmp", "visit_cardio"),
                },
                "pcp": {"type": "simple", "transition": _chain(0.5, "visit_pcp_bmp", "visit_pcp")},
                "visit_cardio": _visit("htn", "hypertension review", "cardiology", ["htn"]),
                "visit_cardio_bmp": _visit(
                    "htn", "hypertension review", "cardiology", ["htn"], ["LAB_BMP"]
                ),
                "visit_pcp": _visit("htn", "hypertension review", "pcp", ["htn"]),
                "visit_pcp_bmp": _visit("htn", "hypertension review", "pcp", ["htn"], ["LAB_BMP"]),
                "gap": {
                    "type": "delay",
                    "delay": _days(365.25 / htn_visits),
                    "transition": {"direct": "pick"},
                },
            },
            "Hypertension review visits, with a basic metabolic panel at about half.",
        )
    )
    treated = float(cal.get("htn.treated"))
    docs.append(
        _doc(
            "payer_htn_meds",
            {
                "start": {"type": "initial", "transition": {"direct": "wait"}},
                "wait": _guard(_active("I10"), "regimen"),
                "regimen": {
                    "type": "delay",
                    "delay": _uniform_days(0, 10),
                    "transition": {"direct": "treat"},
                },
                "treat": {"type": "simple", "transition": _chain(treated, "first_line", "end")},
                "first_line": {
                    "type": "simple",
                    "transition": {
                        "conditional": [
                            {"if": _active("E11.9", "E10.9"), "to": "raas"},
                            {"to": "mixed"},
                        ]
                    },
                },
                "raas": {"type": "simple", "transition": _chain(0.5, "lisinopril", "losartan")},
                "mixed": {
                    "type": "simple",
                    "transition": {
                        "distributed": [
                            {"p": 0.30, "to": "lisinopril"},
                            {"p": 0.20, "to": "losartan"},
                            {"p": 0.30, "to": "amlodipine"},
                            {"p": 0.20, "to": "thiazide"},
                        ]
                    },
                },
                "lisinopril": _order("lisinopril", "htn", "second_pick"),
                "losartan": _order("losartan", "htn", "second_pick"),
                "amlodipine": _order("amlodipine", "htn", "second_pick"),
                "thiazide": _order("hydrochlorothiazide", "htn", "second_pick"),
                "second_pick": {"type": "simple", "transition": _chain(0.35, "second", "end")},
                "second": _order("amlodipine", "htn", "end"),
                "end": {"type": "terminal"},
            },
            "First-line and second-line antihypertensives; a member with diabetes starts an ACE "
            "inhibitor or ARB.",
        )
    )

    # ---- lipid disorders -----------------------------------------------------------------------
    lipid_onset = {
        "start": {"type": "initial", "transition": {"direct": "route"}},
        "route": {
            "type": "simple",
            "transition": {
                "conditional": [
                    {"if": _attr("lipid_start", "==", c), "to": f"onset_{i}"}
                    for i, c in enumerate(LIPID_CODES)
                ]
                + [{"to": "incident"}]
            },
        },
        **{
            f"onset_{i}": {
                "type": "condition_onset",
                "codes": [_onset(c)],
                "transition": {"direct": "end"},
            }
            for i, c in enumerate(LIPID_CODES)
        },
        "end": {"type": "terminal"},
        **_incident_states("lipid", hazard["lipid"], 1.6, {b: "E78.5" for b in BANDS}),
    }
    docs.append(
        _doc(
            "payer_lipid_onset",
            lipid_onset,
            "Who has a lipid disorder on day one and who develops one.",
        )
    )
    docs.append(
        _doc(
            "payer_lipid_reviews",
            {
                "start": {"type": "initial", "transition": {"direct": "wait"}},
                "wait": _guard(_active(*LIPID_CODES), "first"),
                "first": {
                    "type": "delay",
                    "delay": _uniform_days(0, 150),
                    "transition": {"direct": "pick"},
                },
                "pick": {
                    "type": "simple",
                    "transition": _chain(
                        float(cal.get("lipid.panel_per_year")), "visit_lab", "visit"
                    ),
                },
                "visit_lab": _visit(
                    "lipid", "lipid follow-up", "pcp", ["lipid"], ["LAB_LIPID_PANEL"]
                ),
                "visit": _visit("lipid", "lipid follow-up", "pcp", ["lipid"]),
                "gap": {
                    "type": "delay",
                    "delay": _days(365.25 / 1.2),
                    "transition": {"direct": "pick"},
                },
            },
            "Lipid follow-up visits with a lipid panel at about three in four.",
        )
    )
    treated_lipid = float(cal.get("lipid.statin_treated"))
    docs.append(
        _doc(
            "payer_lipid_meds",
            {
                "start": {"type": "initial", "transition": {"direct": "wait"}},
                "wait": _guard(_active(*LIPID_CODES), "regimen"),
                "regimen": {
                    "type": "delay",
                    "delay": _uniform_days(0, 10),
                    "transition": {"direct": "treat"},
                },
                "treat": {"type": "simple", "transition": _chain(treated_lipid, "which", "end")},
                "which": {
                    "type": "simple",
                    "transition": {
                        "distributed": [
                            {"p": 0.50, "to": "atorvastatin"},
                            {"p": 0.25, "to": "rosuvastatin"},
                            {"p": 0.12, "to": "simvastatin"},
                            {"p": 0.13, "to": "pravastatin"},
                        ]
                    },
                },
                "atorvastatin": _order("atorvastatin", "lipid", "add_pick"),
                "rosuvastatin": _order("rosuvastatin", "lipid", "add_pick"),
                "simvastatin": _order("simvastatin", "lipid", "add_pick"),
                "pravastatin": _order("pravastatin", "lipid", "add_pick"),
                "add_pick": {"type": "simple", "transition": _chain(0.10, "ezetimibe", "end")},
                "ezetimibe": _order("ezetimibe", "lipid", "end"),
                "end": {"type": "terminal"},
            },
            "Statin start for the share of lipid-disorder members who are treated, with ezetimibe "
            "for some.",
        )
    )
    return docs
