"""Hierarchical sampling, the Chow-Liu model, plausibility and joint fidelity (#47)."""

from __future__ import annotations

import collections

import numpy as np
import pyarrow as pa
import pyarrow.csv as pcsv
import pytest

import shape
from shape.generation.hierarchy import HierarchicalSampler, hierarchy_violations
from shape.generation.joint_model import fit_joint, joint_fidelity
from shape.generation.reference import register_dataset, unregister_dataset

LEVELS = ("state", "city", "zip")


def _cols(table: pa.Table) -> dict[str, pa.Array]:
    return {n: table[n].combine_chunks() for n in table.column_names}


def _schema(rows: int, **anchor: object) -> dict:
    def col(name: str, typ: str, strategy: str, **gen: object) -> dict:
        return {
            "name": name, "type": typ, "generator": {"strategy": strategy, **gen},
            "nullable": False, "null_rate": 0.0,
        }

    cols = [
        col("id", "integer", "sequence", start=1),
        col("state", "string", "hierarchy", dataset="us_zip", field="state",
            levels=list(LEVELS), **anchor),
        col("city", "string", "hierarchy_field", dataset="us_zip", field="city"),
        col("zip", "string", "hierarchy_field", dataset="us_zip", field="zip"),
        col("lat", "float", "hierarchy_field", dataset="us_zip", field="lat"),
        col("lng", "float", "hierarchy_field", dataset="us_zip", field="lng"),
    ]
    return {
        "schema_version": 1,
        "model": {"name": "geo", "domain": "geo", "schema_mode": "3nf", "locale": "en_US",
                  "seed": 1, "date_range": {"start": "2022-01-01", "end": "2022-12-31"}},
        "tables": {"place": {"name": "place", "primary_key": ["id"],
                             "columns": {c["name"]: c for c in cols}}},
        "relationships": [], "business_rules": [],
        "generation": {"scale": "s", "scales": {"s": {"place": rows}}},
        "correlated_columns": {},
    }


@pytest.fixture
def us_zip(reference: pa.Table):  # noqa: ANN201
    register_dataset("us_zip", reference)
    yield reference
    unregister_dataset("us_zip")


def test_a_generated_table_keeps_every_zip_inside_its_city(us_zip: pa.Table) -> None:
    """The issue's acceptance: 0 mismatches in 2,000 rows."""
    tab = shape.generate(_schema(2000), seed=11).tables["place"]
    assert tab.num_rows == 2000
    ref = _cols(us_zip)
    got = {k: tab[k] for k in ("state", "city", "zip")}
    assert hierarchy_violations(got, ref, ("city", "state", "zip")) == 0
    # the coordinates are the record's own
    coords = set(zip(ref["zip"].to_pylist(), ref["lat"].to_pylist(), ref["lng"].to_pylist(),
                     strict=True))
    assert all(
        (z, la, lo) in coords
        for z, la, lo in zip(tab["zip"].to_pylist(), tab["lat"].to_pylist(),
                             tab["lng"].to_pylist(), strict=True)
    )
    # a table with independent columns would not: shuffling the zips breaks it
    shuffled = dict(got)
    shuffled["zip"] = pa.array(np.random.default_rng(0).permutation(tab["zip"].to_pylist()))
    assert hierarchy_violations(shuffled, ref, ("city", "state", "zip")) > 1500


def test_the_generated_profile_has_no_broken_dependency_against_the_real_one(
    us_zip: pa.Table, city_zip: dict
) -> None:
    gen = shape.generate(_schema(4000), seed=3).tables["place"].select(["city", "state", "zip"])
    gen = gen.set_column(2, "zip", pa.array([int(z) for z in gen["zip"].to_pylist()]))
    real = pcsv.read_csv(city_zip["good"])
    result = shape.diff(shape.profile(real), shape.profile(gen))
    assert not any(c["kind"] in ("dependency_broken", "placeholder_surge") for c in result.changes)


def test_generation_is_deterministic_and_chunk_independent(us_zip: pa.Table) -> None:
    a = shape.generate(_schema(3000), seed=5).tables["place"]
    b = shape.generate(_schema(3000), seed=5).tables["place"]
    assert a.equals(b)
    s = HierarchicalSampler(_cols(us_zip), LEVELS)
    whole = s.sample(1000, 3)
    parts = [s.sample(400, 3, start=0), s.sample(600, 3, start=400)]
    assert pa.concat_arrays([p["zip"] for p in parts]).equals(whole["zip"])
    assert not s.sample(1000, 4)["zip"].equals(whole["zip"])


def test_top_weights_set_the_top_level_and_the_tree_stays_coherent(us_zip: pa.Table) -> None:
    s = HierarchicalSampler(
        _cols(us_zip), LEVELS, weighting="uniform", top_weights={"CA": 0.75, "NY": 0.25}
    )
    out = s.sample(4000, 9)
    c = collections.Counter(out["state"].to_pylist())
    assert set(c) == {"CA", "NY"}
    assert c["CA"] / 4000 == pytest.approx(0.75, abs=0.03)
    assert hierarchy_violations(out, _cols(us_zip), ("city", "state", "zip")) == 0
    with pytest.raises(ValueError, match="top_weights name no value"):
        HierarchicalSampler(_cols(us_zip), LEVELS, top_weights={"ZZ": 1}).sample(5)


def test_records_weighting_matches_the_reference_mix_and_uniform_equalises_levels(
    us_zip: pa.Table,
) -> None:
    ref = collections.Counter(us_zip["state"].to_pylist())
    s = HierarchicalSampler(_cols(us_zip), LEVELS)
    got = collections.Counter(s.sample(20000, 1)["state"].to_pylist())
    top = max(ref, key=ref.get)  # type: ignore[arg-type]
    assert got[top] / 20000 == pytest.approx(ref[top] / len(us_zip), abs=0.01)
    u = collections.Counter(
        HierarchicalSampler(_cols(us_zip), LEVELS, weighting="uniform").sample(20000, 1)["state"]
        .to_pylist()
    )
    assert u[top] / 20000 == pytest.approx(1 / len(ref), abs=0.01)  # every state as likely


def test_a_small_hierarchy_by_hand() -> None:
    cols = {
        "state": pa.array(["A", "A", "A", "B"]),
        "city": pa.array(["x", "x", "y", "z"]),
        "zip": pa.array(["1", "2", "3", "4"]),
    }
    out = HierarchicalSampler(cols, LEVELS).sample(500, 0)
    seen = set(zip(out["state"].to_pylist(), out["city"].to_pylist(), out["zip"].to_pylist(),
                   strict=True))
    assert seen == {("A", "x", "1"), ("A", "x", "2"), ("A", "y", "3"), ("B", "z", "4")}


def test_hierarchy_errors_are_clear(us_zip: pa.Table) -> None:
    with pytest.raises(ValueError, match="no field 'county'"):
        HierarchicalSampler(_cols(us_zip), ("state", "county"))
    with pytest.raises(ValueError, match="at least one level"):
        HierarchicalSampler(_cols(us_zip), ())
    bad = _schema(10)
    bad["tables"]["place"]["columns"]["state"]["generator"].pop("levels")
    with pytest.raises(Exception, match="levels"):
        shape.generate(bad, seed=1)


# ---- the Chow-Liu model ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def city_tables(city_zip: dict) -> tuple[pa.Table, pa.Table]:
    opts = pcsv.ConvertOptions(column_types={"zip": pa.string()})
    return (
        pcsv.read_csv(city_zip["good"], convert_options=opts),
        pcsv.read_csv(city_zip["bad"], convert_options=opts),
    )


def test_plausibility_flags_exactly_the_corrupted_rows(city_tables) -> None:
    good, bad = city_tables
    model = fit_joint(good, max_levels=5000)
    assert model.report(good)["implausible_rate"] <= 0.001  # the 0.999 quantile of its own rows
    assert model.report(good)["impossible_rows"] == 0
    r = model.report(bad)
    assert r["implausible_rate"] == pytest.approx(0.13, abs=0.01)  # 8% 00000 + 5% wrong place
    assert r["impossible_rate"] == pytest.approx(0.05, abs=0.005)  # the wrong-place ZIPs
    assert model.score(bad).mean() > model.score(good).mean()
    flagged = {i for i in r["least_plausible_rows"]}
    assert flagged and all(model.score(bad)[i] > model.threshold() for i in flagged)
    combo = r["impossible_combinations"][0]
    assert combo["columns"] == ["city", "zip"] or combo["columns"] == ["zip", "city"]
    assert combo["rows"] >= 1


def test_per_row_scores_rank_a_wrong_combination_above_a_right_one() -> None:
    n = 2000
    rng = np.random.default_rng(2)
    dept = rng.choice(["cardio", "onco", "neuro"], n)
    drug = np.array([{"cardio": "statin", "onco": "taxol", "neuro": "gabapentin"}[d] for d in dept])
    model = fit_joint({"dept": dept, "drug": drug})
    ok = model.score({"dept": ["cardio"], "drug": ["statin"]})[0]
    wrong = model.score({"dept": ["cardio"], "drug": ["taxol"]})[0]
    assert wrong > ok + 5
    rep = model.report({"dept": ["cardio", "onco"], "drug": ["taxol", "taxol"]})
    assert rep["impossible_rows"] == 1
    assert rep["impossible_combinations"][0]["values"] in (["cardio", "taxol"], ["taxol", "cardio"])


def test_a_mixed_type_model_scores_numbers_through_their_bins() -> None:
    rng = np.random.default_rng(3)
    n = 4000
    sex = rng.choice(["F", "M"], n)
    height = np.where(sex == "F", rng.normal(162, 6, n), rng.normal(177, 7, n))
    model = fit_joint({"sex": sex, "height": height})
    ok = model.score({"sex": ["F", "M"], "height": [160.0, 178.0]})
    odd = model.score({"sex": ["F", "M"], "height": [190.0, 150.0]})
    assert (odd > ok).all()
    drawn = model.sample(3000, seed=1)
    f = np.array([h for s, h in zip(drawn["sex"], drawn["height"], strict=True) if s == "F"])
    m = np.array([h for s, h in zip(drawn["sex"], drawn["height"], strict=True) if s == "M"])
    assert f.mean() < m.mean() - 8  # the dependence between the types survived sampling


def test_joint_sampling_keeps_pairs_seen_in_training(city_tables) -> None:
    good, _ = city_tables
    model = fit_joint(good, max_levels=5000)
    drawn = model.sample(2000, seed=4)
    ref = set(zip(*(good[c].to_pylist() for c in ("city", "state", "zip")), strict=True))
    got = zip(drawn["city"], drawn["state"], drawn["zip"], strict=True)
    assert sum(t not in ref for t in got) == 0
    assert model.sample(50, seed=4) == model.sample(50, seed=4)


def test_joint_fidelity_distances() -> None:
    rng = np.random.default_rng(5)
    n = 5000
    a = rng.choice(["x", "y", "z"], n)
    b = np.where(a == "x", "p", np.where(a == "y", "q", "r"))
    target = {"a": a, "b": b}
    same = joint_fidelity(target, target)
    assert same["max_tvd"] == 0.0 and same["max_hellinger"] == 0.0
    independent = {"a": a, "b": rng.permutation(b)}
    off = joint_fidelity(target, independent)
    assert off["max_tvd"] > 0.4 and off["max_hellinger"] > 0.5 and off["worst_pair"] == ["a", "b"]
    again = joint_fidelity(target, {"a": a, "b": b}, pairs=[("a", "b")])
    assert again["pairs"] == [{"a": "a", "b": "b", "tvd": 0.0, "hellinger": 0.0}]


def test_marginals_alone_cannot_hide_a_broken_pair(city_tables) -> None:
    good, bad = city_tables
    fid = joint_fidelity(good, good, max_levels=5000)
    assert fid["max_tvd"] == 0.0
    # same marginals for city and state, a shuffled pairing: each column is fine, the pair is not
    perm = np.random.default_rng(0).permutation(good.num_rows)
    shuffled = good.set_column(1, "state", good["state"].take(pa.array(perm)))
    assert joint_fidelity(good, shuffled, [("city", "state")], max_levels=5000)["max_tvd"] > 0.5
