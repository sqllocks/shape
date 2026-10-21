"""Name aliases preserve observations and every value/statistical gate."""

import copy
import importlib.util
import sys

import pandas as pd
import pytest

from .test_domain_verify_recorded import BENCH, verifier


def aliases_module():
    sys.path.insert(0, str(BENCH))
    spec = importlib.util.spec_from_file_location("reference_names", BENCH / "reference_names.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def fixture():
    m = aliases_module()
    old = {
        "cpt_codes": [
            {"code": "Old-A", "description": "Old name A", "charge": 10.0, "weight": 0.4},
            {"code": "Old-B", "description": "Old name B", "charge": 20.0, "weight": 0.6},
        ]
    }
    new = copy.deepcopy(old)
    for i, row in enumerate(new["cpt_codes"], 1):
        row.update(code=f"SP-{i:02d}", description=f"Synthetic procedure {i:02d}")
    contract = {
        "owner_commit": m.OWNER_COMMIT,
        "baseline_commit": m.BASELINE_COMMIT,
        "domains": {
            "healthcare": {
                "source": {"cpt_codes": m.digest(old["cpt_codes"])},
                "target": {"cpt_codes": m.digest(new["cpt_codes"])},
                "aliases": {
                    field: [
                        {"lineage": i, "source": m.name_digest(a[field]), "target": b[field]}
                        for i, (a, b) in enumerate(
                            zip(old["cpt_codes"], new["cpt_codes"], strict=True), 1
                        )
                    ]
                    for field in ("code", "description")
                },
            }
        },
    }
    return m, old, new, contract


@pytest.mark.parametrize(
    "control", ["provenance", "domain", "field", "duplicate", "permuted", "weight", "numeric"]
)
def test_alias_registry_fails_closed(control):
    m, old, new, doc = fixture()
    if control == "provenance":
        doc["owner_commit"] = "0" * 40
    if control == "domain":
        doc["domains"]["other"] = doc["domains"].pop("healthcare")
    if control == "field":
        doc["domains"]["healthcare"]["aliases"]["charge"] = []
    if control == "duplicate":
        doc["domains"]["healthcare"]["aliases"]["code"][1]["target"] = "SP-01"
    if control == "permuted":
        rows = doc["domains"]["healthcare"]["aliases"]["code"]
        rows[0]["target"], rows[1]["target"] = rows[1]["target"], rows[0]["target"]
    if control == "weight":
        new["cpt_codes"][0]["weight"] += 0.1
    if control == "numeric":
        new["cpt_codes"][0]["charge"] += 1
    with pytest.raises(m.RecordingError):
        m.ReferenceNames("healthcare", old, new, doc)


def test_aliases_preserve_nulls_counts_numbers_and_input_frames():
    m, old, new, doc = fixture()
    aliases = m.ReferenceNames("healthcare", old, new, doc)
    frame = pd.DataFrame(
        {"cpt_code": ["Old-A", None, "Old-A", "Old-B"], "standard_charge": [10.0, None, 10.0, 20.0]}
    )
    saved = frame.copy(deep=True)
    result = aliases.normalize_frames({"procedure": frame})["procedure"]
    pd.testing.assert_frame_equal(frame, saved)
    pd.testing.assert_series_equal(result["standard_charge"], frame["standard_charge"])
    assert result["cpt_code"].dropna().tolist() == ["SP-01", "SP-01", "SP-02"]
    assert result["cpt_code"].isna().equals(frame["cpt_code"].isna())
    assert sorted(result["cpt_code"].value_counts().tolist()) == [1, 2]
    with pytest.raises(m.RecordingError, match="unknown"):
        aliases.normalize_frames({"procedure": pd.DataFrame({"cpt_code": ["unknown"]})})


@pytest.mark.parametrize(
    "control", ["equal", "frequency", "numeric", "code_description", "charge_pair"]
)
def test_existing_value_and_coherence_checks_remain_effective(control):
    m, old, new, doc = fixture()
    aliases = m.ReferenceNames("healthcare", old, new, doc)
    v = verifier()
    count = 10000
    baseline = pd.DataFrame(
        {
            "id": list(range(count)),
            "cpt_code": ["Old-A", "Old-B"] * (count // 2),
            "cpt_description": ["Old name A", "Old name B"] * (count // 2),
            "standard_charge": [10.0, 20.0] * (count // 2),
        }
    )
    current = aliases.normalize_frames({"procedure": baseline})
    if control == "frequency":
        current["procedure"]["cpt_code"] = "SP-01"
    if control == "numeric":
        current["procedure"]["standard_charge"] *= 2
    if control == "code_description":
        current["procedure"]["cpt_description"] = "Synthetic procedure 01"
    if control == "charge_pair":
        current["procedure"]["standard_charge"] = [20.0, 10.0] * (count // 2)
    raw = {
        "domain": "healthcare",
        "relationships": [],
        "tables": {
            "procedure": {
                "primary_key": ["id"],
                "columns": {
                    "id": {"generator": {"strategy": "sequence"}},
                    **{
                        col: {
                            "generator": {
                                "strategy": "record_sample" if field == "code" else "record_field",
                                "dataset": "cpt_codes",
                                "field": field,
                            }
                        }
                        for col, field in [
                            ("cpt_code", "code"),
                            ("cpt_description", "description"),
                            ("standard_charge", "charge"),
                        ]
                    },
                },
            }
        },
    }
    types = {
        "procedure": {col: "double" if col == "standard_charge" else "string" for col in baseline}
    }

    class Pools:
        def dataset(self, name, table=None):
            return old[name]

        def effective(self, t, c, gen):
            return gen

        def pool_for(self, gen, table=None):
            return {str(row[gen["field"]]) for row in old["cpt_codes"]} if "field" in gen else None

        def component_fn(self, gen):
            return None

    ref = ({"procedure": baseline}, types, ["procedure"])
    candidate = (current, types, ["procedure"])
    report = v.compare_runs(raw, ref, candidate, [copy.deepcopy(ref)] * 4, Pools(), aliases=aliases)
    assert bool(report["flagged"]) == (control != "equal")
    if control in ("code_description", "charge_pair"):
        assert any("record_coherence" in f for f in report["flagged"])
