"""``diff`` and contract rules on the joint analysis (#47): the issue's acceptance."""

from __future__ import annotations

import pytest

import shape
from shape.contracts.v1 import ContractError


@pytest.fixture(scope="module")
def profiles(city_zip: dict) -> tuple[shape.api.Profile, shape.api.Profile]:  # type: ignore[name-defined]
    return shape.profile(city_zip["good"]), shape.profile(city_zip["bad"])


def _by_kind(result: object) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for c in result.changes:  # type: ignore[attr-defined]
        out.setdefault(c["kind"], []).append(c)
    return out


def test_the_example_is_drift_that_names_the_dependency_and_the_value(profiles) -> None:
    good, bad = profiles
    result = shape.diff(good, bad)
    assert result.drifted
    kinds = _by_kind(result)
    broken = {c["column"]: c for c in kinds["dependency_broken"]}
    zc = broken["zip -> city"]
    assert zc["severity"] == "high"
    assert zc["baseline"] == 1.0 and zc["current"] == pytest.approx(0.87575)
    assert zc["detail"]["violating_groups"] == 178
    # the placeholder responsible is named, with its share
    assert zc["detail"]["placeholders"][0]["value"] == "0"
    assert zc["detail"]["placeholders"][0]["share_of_rows"] == pytest.approx(0.08)
    assert "'0'" in zc["message"] and "zip" in zc["message"] and "city" in zc["message"]
    surge = kinds["placeholder_surge"][0]
    assert surge["column"] == "zip" and surge["detail"]["value"] == "0"
    assert (surge["baseline"], surge["current"]) == (0.0, pytest.approx(0.08))
    assert kinds["implausible_rate_change"][0]["current"] == pytest.approx(0.08)


def test_no_change_when_the_profiles_are_the_same(profiles) -> None:
    good, _ = profiles
    assert not shape.diff(good, good).drifted


def test_a_stable_dependency_is_not_drift(profiles) -> None:
    # city -> state holds the same in both files (same cities, same states)
    result = shape.diff(*profiles)
    assert all(c["column"] != "city -> state" for c in result.changes)


def test_policy_ignore_and_thresholds_apply(profiles) -> None:
    result = shape.diff(*profiles, ignore_columns=["zip"])
    assert not any(c["kind"] in ("dependency_broken", "placeholder_surge") for c in result.changes)
    result = shape.diff(
        *profiles, thresholds={"placeholder_share": 0.5, "dependency_confidence": 0.5}
    )
    assert not any(c["kind"] in ("dependency_broken", "placeholder_surge") for c in result.changes)
    only = shape.diff(*profiles, only_columns=["city"])
    assert any(c["kind"] == "dependency_broken" for c in only.changes)  # a column of the pair


def test_min_severity_filters_the_new_kinds(profiles) -> None:
    result = shape.diff(*profiles, thresholds={"min_severity": "high"})
    assert {c["kind"] for c in result.changes} == {"dependency_broken"}


def test_a_determinant_that_becomes_a_key_is_not_a_break(profiles) -> None:
    good, bad = profiles
    # the reverse direction: the dependency of the baseline is absent from the current profile only
    # because the determinant is unique there, which is an improvement
    result = shape.diff(bad, good)
    assert not any(c["kind"] == "dependency_broken" for c in result.changes)


def test_older_profiles_without_joint_entries_diff_as_before(profiles) -> None:
    import copy

    from shape.profile.reference.profile import Profile

    def strip(p: Profile) -> Profile:
        d = copy.deepcopy(p.to_dict())
        d.pop("joint", None)
        for col in d["columns"].values():
            col.pop("placeholders", None)
        return Profile(d)

    good, bad = (strip(p) for p in profiles)
    kinds = {c["kind"] for c in shape.diff(good, bad).changes}
    assert kinds == {"uniqueness_change"}  # what diff reported before #47


CONTRACT = {
    "fd": [
        {"determinant": "zip", "dependent": "city", "min_confidence": 0.99},
        {"determinant": "city", "dependent": "state", "min_confidence": 0.8},
    ],
    "max_implausible_rate": 0.02,
    "columns": {"zip": {"no_placeholder": True}},
}


def test_contract_rules_pass_the_good_data_and_fail_the_bad(profiles) -> None:
    good, bad = profiles
    assert shape.check(good, CONTRACT).passed
    result = shape.check(bad, CONTRACT)
    assert not result.passed
    rules = {v["rule"]: v for v in result.violations}
    assert rules["no_placeholder"]["column"] == "zip"
    assert rules["no_placeholder"]["observed"][0]["value"] == "0"
    assert rules["fd"]["column"] == "zip -> city"
    assert rules["fd"]["observed"]["confidence"] == pytest.approx(0.87575)
    assert rules["max_implausible_rate"]["observed"]["implausible_rate"] == pytest.approx(0.08)
    assert len(result.violations) == 3  # city -> state (0.8875 >= 0.8) holds


def test_no_placeholder_object_form_allows_values_and_shares(profiles) -> None:
    _, bad = profiles
    allow = {"columns": {"zip": {"no_placeholder": {"allow": ["0"]}}}}
    assert shape.check(bad, allow).passed
    cap = {"columns": {"zip": {"no_placeholder": {"max_share": 0.1}}}}
    assert shape.check(bad, cap).passed
    tight = {"columns": {"zip": {"no_placeholder": {"max_share": 0.05}}}}
    assert not shape.check(bad, tight).passed
    assert shape.check(bad, {"columns": {"zip": {"no_placeholder": False}}}).passed


def test_implies_reads_the_conditional_tables() -> None:
    import pyarrow as pa

    n = 600
    t = pa.table(
        {
            "dept": pa.array(["cardio", "onco", "neuro"] * (n // 3)),
            "ward": pa.array(["A", "B", "C"] * (n // 3)),
        }
    )
    p = shape.profile(t)
    ok = {
        "implies": [
            {
                "if": {"column": "dept", "equals": "cardio"},
                "then": {"column": "ward", "equals": "A"},
                "min_confidence": 0.99,
            }
        ]
    }
    assert shape.check(p, ok).passed
    wrong = {
        "implies": [
            {
                "if": {"column": "dept", "equals": "cardio"},
                "then": {"column": "ward", "equals": "B"},
                "min_confidence": 0.5,
            }
        ]
    }
    v = shape.check(p, wrong).violations
    assert v[0]["rule"] == "implies" and v[0]["observed"]["confidence"] == 0.0


def test_a_rule_the_profile_cannot_test_is_a_violation_not_a_pass(profiles) -> None:
    good, _ = profiles
    unknown = {"fd": [{"determinant": "state", "dependent": "zip", "min_confidence": 0.9}]}
    result = shape.check(good, unknown)  # zip is a unique key in `good`: nothing determines it
    assert not result.passed
    missing = {"fd": [{"determinant": "nope", "dependent": "zip", "min_confidence": 0.9}]}
    assert shape.check(good, missing).violations[0]["observed"] == {"missing_columns": ["nope"]}


@pytest.mark.parametrize(
    "bad_contract",
    [
        {"fd": {"determinant": "a"}},
        {"fd": [{"determinant": "a", "dependent": "b"}]},
        {"fd": [{"determinant": "a", "dependent": "b", "min_confidence": 1.5}]},
        {"fd": [{"determinant": 3, "dependent": "b", "min_confidence": 0.9}]},
        {"fd": [{"determinant": "a", "dependent": "b", "min_confidence": 0.9, "x": 1}]},
        {
            "implies": [
                {"if": {"column": "a"}, "then": {"column": "b", "equals": 1}, "min_confidence": 1}
            ]
        },
        {"max_implausible_rate": 2},
        {"columns": {"a": {"no_placeholder": "yes"}}},
        {"columns": {"a": {"no_placeholder": {"max_share": 3}}}},
    ],
)
def test_malformed_joint_rules_are_contract_errors(profiles, bad_contract) -> None:
    with pytest.raises(ContractError):
        shape.check(profiles[0], bad_contract)


def test_v1_contracts_without_the_new_rules_behave_as_before(profiles) -> None:
    good, bad = profiles
    plain = {"row_count": {"min": 1000}, "columns": {"city": {"dtype": "string"}}}
    assert shape.check(good, plain).passed and shape.check(bad, plain).passed
    with pytest.raises(ContractError, match="unknown rules"):
        shape.check(good, {"columns": {"city": {"no_such_rule": 1}}})


def test_the_rules_work_per_table_in_a_dataset(city_zip: dict) -> None:
    ds = shape.profile({"places": city_zip["bad"]})
    result = shape.check(ds, {"tables": {"places": CONTRACT}})
    assert {(v["column"], v["rule"]) for v in result.violations} == {
        ("places.zip", "no_placeholder"),
        ("places.zip -> city", "fd"),
        (None, "places:max_implausible_rate"),
    }


# ---- reference pairs ------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ref_csv(reference, tmp_path_factory):  # noqa: ANN001, ANN201
    import pyarrow.csv as pcsv

    path = tmp_path_factory.mktemp("ref") / "zips.csv"
    pcsv.write_csv(reference.select(["zip", "city", "state"]), path)
    return path


PAIR = {"columns": ["city", "state", "zip"], "reference": "zips.csv", "min_match_rate": 0.99}


def test_reference_pairs_find_the_wrong_place_zips_the_format_checks_cannot(
    city_zip, ref_csv
) -> None:
    spec = [{"columns": ["city", "state", "zip"], "reference": str(ref_csv)}]
    good = shape.profile(city_zip["good"], reference_pairs=spec)
    bad = shape.profile(city_zip["bad"], reference_pairs=spec)
    g = good.to_dict()["joint"]["reference_pairs"][0]
    b = bad.to_dict()["joint"]["reference_pairs"][0]
    assert g["match_rate"] == 1.0 and g["mismatched"] == 0
    # 8% 00000 (not a real ZIP) + 5% real ZIPs of another place: both are outside the reference
    assert b["match_rate"] == pytest.approx(0.87, abs=0.005)
    assert b["mismatched"] == 520 and b["examples"][0]["values"][2] == "0"
    assert shape.check(good, {"reference_pair": [PAIR]}).passed
    result = shape.check(bad, {"reference_pair": [PAIR]})
    v = result.violations[0]
    assert v["rule"] == "reference_pair" and v["observed"]["mismatched"] == 520
    change = next(c for c in shape.diff(good, bad).changes if c["kind"] == "reference_match_change")
    assert change["baseline"] == 1.0 and change["current"] == pytest.approx(0.87, abs=0.005)
    assert change["severity"] == "high"


def test_a_reference_pair_the_profile_did_not_measure_is_a_violation(city_zip) -> None:
    result = shape.check(shape.profile(city_zip["good"]), {"reference_pair": [PAIR]})
    assert "not measured" in result.violations[0]["observed"]


def test_reference_pair_inputs_are_validated(city_zip, ref_csv) -> None:
    with pytest.raises(ValueError, match="no column 'nope'"):
        shape.profile(
            city_zip["good"], reference_pairs=[{"columns": ["nope"], "reference": str(ref_csv)}]
        )
    with pytest.raises(ValueError, match="no field 'nope'"):
        shape.profile(
            city_zip["good"],
            reference_pairs=[{"columns": {"zip": "nope"}, "reference": str(ref_csv)}],
        )
    with pytest.raises(ContractError):
        shape.check(shape.profile(city_zip["good"]), {"reference_pair": [{"columns": ["a"]}]})


def test_the_cli_takes_reference_pairs(city_zip, ref_csv, tmp_path, capsys) -> None:
    from shape.cli.main import main

    out = tmp_path / "bad.shape"
    rc = main(
        [
            "profile",
            str(city_zip["bad"]),
            "-o",
            str(out),
            "--reference-pair",
            f"city,state,zip={ref_csv}",
        ]
    )
    assert rc == 0
    pair = shape.load(str(out)).to_dict()["joint"]["reference_pairs"][0]
    assert pair["columns"] == ["city", "state", "zip"] and pair["mismatched"] == 520
