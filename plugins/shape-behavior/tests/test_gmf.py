"""Import of Generic Module Framework JSON: a hand-written fixture, reported gaps, strictness."""

import json
from pathlib import Path

import pytest
from helpers import counts
from shape_behavior import Population, SimConfig, Simulator, import_gmf, load_module
from shape_behavior.gmf import UnsupportedGmfError, is_gmf

FIXTURES = Path(__file__).parent / "fixtures"
EXAMPLE = FIXTURES / "gmf_example.json"
UNSUPPORTED = FIXTURES / "gmf_unsupported.json"

POP = {
    "age_at_start": {"kind": "uniform", "low": 0, "high": 60},
    "attributes": {"gender": {"kind": "categorical", "values": {"F": 0.5, "M": 0.5}}},
}


def _run(module, n=4000, until="2024-01-01", seed=3):
    sim = Simulator([module], Population(size=n, start="2020-01-01", **POP), SimConfig(seed=seed))
    return sim.run_until(until)


def test_fixture_is_recognised_and_imports_cleanly():
    assert is_gmf(json.loads(EXAMPLE.read_text(encoding="utf-8")))
    result = import_gmf(EXAMPLE)
    assert result.unsupported == []
    assert result.module.name == "Example Wellness Follow-up"
    assert len(result.module.states) == 15
    assert (
        any("wellness" in w for w in result.warnings) is False
    )  # the encounter is not a wellness one


def test_import_accepts_path_string_json_text_and_dict():
    text = EXAMPLE.read_text(encoding="utf-8")
    digests = {
        import_gmf(EXAMPLE).module.digest(),
        import_gmf(str(EXAMPLE)).module.digest(),
        import_gmf(text).module.digest(),
        import_gmf(json.loads(text)).module.digest(),
        load_module(EXAMPLE).digest(),
    }
    assert len(digests) == 1


def test_imported_module_runs_with_the_expected_shape():
    module = import_gmf(EXAMPLE).module
    n = 4000
    t = _run(module, n=n)
    c = counts(t, "kind")
    adults = c["encounter"]
    assert 0.3 * n < adults < 0.9 * n  # ages 0-60 uniform: about 70% are 18 or older at some point
    assert c["observation"] == adults
    onset_share = c["condition_onset"] / adults
    assert abs(onset_share - 0.25) < 0.05
    assert c["medication_order"] == c["condition_onset"]
    # the observation value stays in its range and keeps the unit
    obs = t.filter(
        __import__("pyarrow.compute", fromlist=["x"]).equal(t.column("kind"), "observation")
    )
    values = obs.column("value").to_pylist()
    assert min(values) >= 80 and max(values) <= 200
    assert set(obs.column("unit").to_pylist()) == {"mg/dL"}
    assert set(obs.column("code").to_pylist()) == {"OBS-1"}


def test_the_guard_makes_entities_wait_until_they_are_adults():
    module = import_gmf(EXAMPLE).module
    sim = Simulator([module], Population(size=3000, start="2020-01-01", **POP), SimConfig(seed=3))
    t = sim.run_until("2030-01-01")
    born = {r["entity_id"]: r["born"] for r in sim.entities().to_pylist()}
    first_visit = {}
    for row in t.to_pylist():
        if row["kind"] == "encounter" and row["entity_id"] not in first_visit:
            first_visit[row["entity_id"]] = row["time"]
    start = __import__("datetime").datetime(2020, 1, 1)
    for (
        eid,
        b,
    ) in (
        born.items()
    ):  # adults well before 2030-01-01 (18, plus up to 3 months of waiting) have all visited
        if (start - b).days / 365.25 >= 8.5:
            assert eid in first_visit
    for eid, when in first_visit.items():
        assert (when - born[eid]).days / 365.25 >= 18 - 1e-6


def test_unsupported_elements_are_reported_not_silently_dropped():
    result = import_gmf(UNSUPPORTED)
    found = {(u.state, u.type) for u in result.unsupported}
    assert ("Wear_Device", "Device") in found
    assert ("Sub", "CallSubmodule") in found
    assert any(u.type == "condition Socioeconomic Status" for u in result.unsupported)
    text = result.report()
    assert "unsupported (3)" in text and "Device" in text and "CallSubmodule" in text
    # lenient import still runs: unsupported states pass through, the unsupported condition is false
    t = _run(result.module, n=50)
    assert counts(t, "kind").get("encounter", 0) == 0


def test_strict_import_raises_on_the_first_unsupported_element():
    with pytest.raises(UnsupportedGmfError, match="Wear_Device"):
        import_gmf(UNSUPPORTED, strict=True)
    with pytest.raises(UnsupportedGmfError):
        load_module(UNSUPPORTED, strict=True)


def test_load_module_keeps_the_report_on_the_module():
    module = load_module(UNSUPPORTED)
    assert module.import_report is not None and len(module.import_report.unsupported) == 3


def test_probabilities_that_do_not_sum_to_one_are_normalised_with_a_warning():
    doc = {
        "name": "norm",
        "states": {
            "Initial": {
                "type": "Initial",
                "distributed_transition": [
                    {"distribution": 0.2, "transition": "A"},
                    {"distribution": 0.2, "transition": "B"},
                ],
            },
            "A": {"type": "Terminal"},
            "B": {"type": "Terminal"},
        },
    }
    result = import_gmf(doc)
    assert any("normalized" in w for w in result.warnings)


def test_a_native_document_is_not_mistaken_for_gmf():
    with pytest.raises(ValueError, match="Generic Module Framework"):
        import_gmf({"format": "shape-behavior/1", "name": "x", "states": {}})
