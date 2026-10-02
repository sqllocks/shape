"""Joint analysis inside ``shape.profile``: dependencies, keys, associations, conditional tables,
and its bounds (#47)."""

from __future__ import annotations

import time

import numpy as np
import pyarrow as pa
import pytest

import shape
from shape.profile.joint import analyze
from shape.profile.joint import measures as M


def _table(profile: object) -> dict:
    d = profile.to_dict()  # type: ignore[attr-defined]
    return next(iter(d["tables"].values())) if "tables" in d else d


def _fds(profile: object) -> dict[tuple[str, str], dict]:
    j = _table(profile)["joint"]
    return {(e["determinant"][0], e["dependent"]): e for e in j["dependencies"]}


def test_the_example_reports_the_dependency_that_broke(city_zip: dict) -> None:
    good = shape.profile(city_zip["good"])
    bad = shape.profile(city_zip["bad"])
    gj, bj = _table(good)["joint"], _table(bad)["joint"]
    assert ("zip", "city") not in _fds(good)  # a unique determinant: held trivially
    broken = _fds(bad)[("zip", "city")]
    assert broken["confidence"] == pytest.approx(0.87575)  # the same number `shape fd` gives
    assert broken["violating_groups"] == 178
    assert broken["violations"][0]["determinant_value"] == "0"  # the placeholder, read as 0
    assert broken["violations"][0]["rows"] == 320
    assert gj["implausible_rate"] == 0.0
    assert bj["implausible_rate"] == pytest.approx(0.08)  # the 8% placeholder rows
    assert bj["implausible_by_placeholder"] == pytest.approx(0.08)


def test_a_dependency_matches_the_fd_command(city_zip: dict) -> None:
    from shape.profile.dependencies import functional_dependency

    rows = list(
        zip(*(np.asarray(c.to_pylist()) for c in _csv(city_zip["bad"]).columns), strict=True)
    )
    ref = functional_dependency(
        [{"city": c, "state": s, "zip": int(z)} for c, s, z in rows], ("zip",), "city"
    )
    ours = _fds(shape.profile(city_zip["bad"]))[("zip", "city")]
    assert ours["confidence"] == pytest.approx(ref.confidence)
    assert ours["violating_groups"] == ref.violating_groups


def _csv(path):  # noqa: ANN001, ANN202
    import pyarrow.csv as pcsv

    return pcsv.read_csv(
        path, convert_options=pcsv.ConvertOptions(column_types={"zip": pa.int64()})
    )


def test_every_input_kind_gets_the_same_joint_analysis(city_zip: dict, tmp_path) -> None:
    import pyarrow.parquet as pq

    t = _csv(city_zip["bad"])
    pq.write_table(t, tmp_path / "bad.parquet")
    expect = _table(shape.profile(city_zip["bad"]))["joint"]
    for source in (tmp_path / "bad.parquet", t, t.to_pandas() if _has_pandas() else t):
        got = _table(shape.profile(source))["joint"]
        assert got["dependencies"] == expect["dependencies"]
        assert got["implausible_rate"] == expect["implausible_rate"]
    multi = shape.profile({"a": t, "b": t}).to_dict()["tables"]
    assert multi["a"]["joint"]["dependencies"] == expect["dependencies"]


def _has_pandas() -> bool:
    try:
        import pandas  # noqa: F401
    except ImportError:
        return False
    return True


def test_joint_analysis_can_be_switched_off(city_zip: dict, monkeypatch) -> None:
    monkeypatch.setenv("SHAPE_PROFILE_JOINT", "0")
    assert "joint" not in _table(shape.profile(city_zip["bad"]))


def test_associations_cover_every_type_pair() -> None:
    rng = np.random.default_rng(1)
    n = 3000
    a = rng.integers(0, 5, n)
    t = pa.table(
        {
            "grp": pa.array([f"g{i}" for i in a]),
            "twin": pa.array([f"t{i}" for i in a]),  # one-to-one with grp
            "noise": pa.array([f"n{i}" for i in rng.integers(0, 4, n)]),
            "x": a * 10.0 + rng.normal(0, 1, n),  # follows grp
            "y": a * 2.0 + rng.normal(0, 1, n),
            "z": rng.normal(0, 1, n),
        }
    )
    j = _table(shape.profile(t))["joint"]
    by = {(e["a"], e["b"], e["kind"]): e for e in j["associations"]}
    cat = by[("grp", "twin", "categorical")]
    assert cat["cramers_v"] > 0.95 and cat["theil_u_a_given_b"] > 0.95
    assert ("grp", "noise", "categorical") not in by  # independent: below the floor
    num = by[("x", "y", "numeric")]
    assert num["pearson"] > 0.9 and num["spearman"] > 0.9 and num["kendall"] > 0.7
    mixed = by[("grp", "x", "categorical-numeric")]
    assert mixed["correlation_ratio"] > 0.95 and mixed["mutual_information"] > 0.5
    # conditional probability table: P(twin | grp) is a point mass
    cond = next(c for c in j["conditionals"] if c["given"] == "grp" and c["target"] == "twin")
    assert all(max(r["p"].values()) == 1.0 for r in cond["table"].values())


def test_theil_u_is_asymmetric_and_v_is_symmetric() -> None:
    # city determines state, not the reverse
    city = np.repeat(np.arange(6), 100)
    state = city // 3
    t = M.contingency(city, state, 6, 2)
    u_city_given_state, u_state_given_city = M.theil_u(t)
    assert u_state_given_city == pytest.approx(1.0)
    assert u_city_given_state < 0.5
    assert M.cramers_v(t) == pytest.approx(M.cramers_v(t.T))


def test_rank_correlations_on_known_data() -> None:
    x = np.arange(100, dtype=float)
    assert M.spearman(x, x**3) == pytest.approx(1.0)
    assert M.kendall_tau(x, -x) == pytest.approx(-1.0)
    assert M.spearman(x, np.zeros(100)) is None
    ties = np.array([1.0, 1.0, 2.0, 3.0, 3.0, 3.0])
    assert list(M.ranks(ties)) == [1.5, 1.5, 3.0, 5.0, 5.0, 5.0]


def test_a_two_column_candidate_key_is_found() -> None:
    n = 400
    t = pa.table(
        {
            "region": pa.array([f"r{i % 20}" for i in range(n)]),
            "store": pa.array([f"s{i // 20}" for i in range(n)]),
            "other": pa.array([f"o{i % 7}" for i in range(n)]),
        }
    )
    keys = _table(shape.profile(t))["joint"]["keys"]
    assert {"fields": ["region", "store"], "rows": n, "distinct": n, "exact": True} in keys


def test_the_cost_does_not_grow_with_the_table() -> None:
    rng = np.random.default_rng(0)
    cols = {f"c{i}": pa.array([f"v{x}" for x in rng.integers(0, 30, 40_000)]) for i in range(80)}
    wide = pa.table(cols)
    t0 = time.perf_counter()
    j = analyze.analyze_table(_cols(wide), 40_000)
    elapsed = time.perf_counter() - t0
    budget = analyze.budget_for(40_000)
    assert budget is analyze.LARGE
    assert j is not None and j["sampled"] is True and j["rows_analyzed"] == budget.sample_rows
    assert len(j["columns"]) <= 2 * budget.max_columns
    assert j["dependency_pairs_evaluated"] <= budget.max_fd_pairs
    assert len(j["associations"]) <= analyze.MAX_ASSOCIATIONS
    assert len(j["conditionals"]) <= analyze.MAX_CONDITIONALS
    assert elapsed < 10.0


def _cols(table: pa.Table):  # noqa: ANN202
    from shape.profile.reference.readers import _arrow_cols

    return _arrow_cols(table)


def test_tiny_and_single_column_tables_have_no_joint_entry() -> None:
    assert "joint" not in _table(shape.profile(pa.table({"a": list(range(100))})))
    assert "joint" not in _table(shape.profile(pa.table({"a": [1, 2, 3], "b": [1, 2, 3]})))


def test_the_joint_entry_is_deterministic_and_survives_save_and_load(
    city_zip: dict, tmp_path
) -> None:
    a = shape.profile(city_zip["bad"])
    b = shape.profile(city_zip["bad"])
    assert _table(a)["joint"] == _table(b)["joint"]
    shape.save(a, str(tmp_path / "p.shape"))
    again = shape.load(str(tmp_path / "p.shape"))
    assert _table(again)["joint"] == _table(a)["joint"]


def test_the_privacy_safe_profile_carries_no_joint_values(city_zip: dict) -> None:
    """``joint`` and ``placeholders`` hold values (violating groups, conditional tables): the safe
    profile is built from allow-listed fields and leaves them out."""
    import json

    from shape.privacy.safe_profile import SafeConfig, to_safe_profile

    profile = shape.profile(city_zip["bad"])
    assert "violations" in json.dumps(profile.to_dict())
    safe = json.dumps(to_safe_profile(profile, SafeConfig()).to_dict(), default=str)
    assert "joint" not in safe and "placeholders" not in safe and "violations" not in safe


def test_two_column_keys_agree_with_the_key_command() -> None:
    from shape.profile.dependencies import candidate_key

    n = 400
    rows = [{"region": f"r{i % 20}", "store": f"s{i // 20}"} for i in range(n)]
    assert candidate_key(rows, ("region", "store")).unique
    t = pa.table({k: [r[k] for r in rows] for k in ("region", "store")})
    keys = _table(shape.profile(t))["joint"]["keys"]
    assert keys and keys[0]["fields"] == ["region", "store"]


def test_the_new_fields_are_the_only_difference_they_make(city_zip: dict, monkeypatch) -> None:
    """Every field the parity check compares is unchanged: with the joint analysis on or off, the
    profile differs only by ``joint`` (and ``placeholders``, which depend on no switch)."""
    on = _table(shape.profile(city_zip["bad"]))
    monkeypatch.setenv("SHAPE_PROFILE_JOINT", "0")
    off = _table(shape.profile(city_zip["bad"]))
    assert "joint" in on and "joint" not in off
    on = {k: v for k, v in on.items() if k != "joint"}
    assert on == off
