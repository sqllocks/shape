"""W2-01 (issue #61): mergeable profiles.

Deliverables, each with its tests below:

1. statistics that combine: ``merge(a, b)`` equals profiling the union (exactly for the exact
   statistics, within the sketch's documented error for the sketches);
2. ``shape profile merge`` and the Python API; merged profiles carry their inputs' content ids;
3. tests against whole-data profiles, including empty and single-row partitions;
4. documented error bounds for each sketch.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest

import shape
from shape.artifact.io import ArtifactError, read_artifact, write_artifact
from shape.cli.main import main as cli_main
from shape.profile import MergeError, merge_profiles
from shape.profile.error import hll_error, kll_error, space_saving_error

ROOT = Path(__file__).resolve().parents[2]
EXACT_FIELDS = ("null_count", "null_rate", "min_value", "max_value")
DROPPED = (
    "is_enum",
    "enum_values",
    "distribution",
    "distribution_params",
    "pattern",
    "outlier_rate",
    "fit_score",
    "value_counts_ext",
    "string_length",
)


def _data(n: int = 6000, seed: int = 7) -> pa.Table:
    rng = np.random.default_rng(seed)
    weights = 1.0 / np.arange(1, 41)
    words = np.array([f"w{i:02d}" for i in range(40)])
    return pa.table(
        {
            "id": pa.array(np.arange(n)),
            "x": pa.array(rng.lognormal(size=n), mask=rng.random(n) < 0.1),
            "cat": pa.array(words[rng.choice(40, n, p=weights / weights.sum())]),
            "flag": pa.array(rng.random(n) < 0.3),
            "ts": pa.array(rng.integers(0, 10**9, n), type=pa.timestamp("s")),
        }
    )


CUTS = [0, 0, 1, 1500, 1501, 3200, 6000]  # an empty and a single-row partition among them


def _parts(table: pa.Table, cuts=CUTS) -> list[pa.Table]:
    return [table.slice(a, b - a) for a, b in zip(cuts, cuts[1:], strict=False)]


def _profiles(parts, sketches=True):
    return [shape.profile(p, name=f"part{i}", sketches=sketches) for i, p in enumerate(parts)]


@pytest.fixture(scope="module")
def table() -> pa.Table:
    return _data()


@pytest.fixture(scope="module")
def whole(table):
    return shape.profile(table, name="whole").to_dict()


@pytest.fixture(scope="module")
def sketched(table):
    return _profiles(_parts(table))


def _cols(p) -> dict:
    return p.to_dict()["columns"]


def _close(a, b, rel=1e-9):
    return math.isclose(a, b, rel_tol=rel, abs_tol=1e-12)


# ---------------------------------------------------------------- 1. opt-in sketch state


def test_default_profile_is_unchanged_by_the_feature(table, tmp_path):
    plain = shape.profile(table, name="t")
    withs = shape.profile(table, name="t", sketches=True)
    assert plain.sketches is None
    assert withs.sketches is not None
    # the body, and therefore the content id, are the same with or without sketch state
    assert plain.to_dict() == withs.to_dict()
    assert plain.content_id == withs.content_id
    shape.save(plain, tmp_path / "p.shape")
    manifest, parts = read_artifact(str(tmp_path / "p.shape"), notice=False)
    assert set(parts) == {"profile.json"}
    assert "sketches" not in manifest
    assert manifest["shape_content_id"] == plain.content_id


def test_sketch_state_is_a_versioned_optional_component(table, tmp_path):
    prof = shape.profile(table, name="t", sketches=True)
    cid = shape.save(prof, tmp_path / "s.shape")
    assert cid == prof.content_id
    manifest, parts = read_artifact(str(tmp_path / "s.shape"), notice=False)
    assert set(parts) == {"profile.json", "sketches.json"}
    assert manifest["sketches"] == {"format": "shape-profile-sketches", "version": 1}
    again = shape.load(tmp_path / "s.shape")
    assert again == prof
    assert again.sketches == prof.sketches
    doc = prof.sketches
    assert doc["format"] == "shape-profile-sketches" and doc["version"] == 1
    assert set(doc["tables"]) == {"t"}


def test_a_newer_sketch_version_is_refused_with_a_clear_error(table, tmp_path):
    prof = shape.profile(table, name="t", sketches=True)
    shape.save(prof, tmp_path / "s.shape")
    manifest, parts = read_artifact(str(tmp_path / "s.shape"), notice=False)
    doc = json.loads(parts["sketches.json"])
    doc["version"] = 2
    parts["sketches.json"] = json.dumps(doc).encode()
    manifest["sketches"] = {"format": "shape-profile-sketches", "version": 2}
    write_artifact(
        str(tmp_path / "new.shape"),
        {k: v for k, v in manifest.items() if k != "content_hashes"},
        parts,
    )
    with pytest.raises(ArtifactError, match="newer.*upgrade|version 2"):
        shape.load(tmp_path / "new.shape")


def test_corrupt_sketch_state_is_refused(table, tmp_path):
    prof = shape.profile(table, name="t", sketches=True)
    shape.save(prof, tmp_path / "s.shape")
    manifest, parts = read_artifact(str(tmp_path / "s.shape"), notice=False)
    parts["sketches.json"] = b"{not json"
    write_artifact(
        str(tmp_path / "bad.shape"),
        {k: v for k, v in manifest.items() if k != "content_hashes"},
        parts,
    )
    with pytest.raises(ArtifactError, match="sketches"):
        shape.load(tmp_path / "bad.shape")


def test_sketches_for_csv_dataset_and_delta_free_sources(tmp_path):
    t = _data(500)
    csv = tmp_path / "a.csv"
    import pyarrow.csv as pacsv

    pacsv.write_csv(t, csv)
    assert shape.profile(str(csv), sketches=True).sketches is not None
    ds = shape.profile({"a": t, "b": t.slice(0, 10)}, sketches=True)
    assert set(ds.sketches["tables"]) == {"a", "b"}


# ---------------------------------------------------------------- 2/3. exact statistics


def test_merged_exact_statistics_equal_the_whole(sketched, whole):
    merged = merge_profiles(sketched, name="whole")
    m = merged.to_dict()
    assert m["row_count"] == whole["row_count"]
    assert set(m["columns"]) == set(whole["columns"])
    for name, w in whole["columns"].items():
        c = m["columns"][name]
        assert c["dtype"] == w["dtype"], name
        for f in EXACT_FIELDS:
            assert c[f] == w[f], (name, f)
        for f in ("mean", "std"):
            if w[f] is None:
                assert c[f] is None
            else:
                assert _close(c[f], w[f]), (name, f, c[f], w[f])


def test_exact_only_merge_needs_no_sketches(table, whole):
    plain = _profiles(_parts(table), sketches=False)
    merged = merge_profiles(plain, exact_only=True, name="whole")
    m = merged.to_dict()
    assert m["row_count"] == whole["row_count"]
    for name, w in whole["columns"].items():
        c = m["columns"][name]
        assert c["null_count"] == w["null_count"]
        assert c["min_value"] == w["min_value"] and c["max_value"] == w["max_value"]
        # what needs a sketch (or the data) is unknown, never a made-up number
        for f in ("cardinality", "cardinality_ratio", "is_unique", "quantiles", *DROPPED):
            assert c[f] is None, (name, f)
    assert merged.sketches is None
    assert m["merge"]["mode"] == "exact-only"


def test_merge_without_sketches_is_a_clear_error_for_the_rest(table):
    plain = _profiles(_parts(table), sketches=False)
    with pytest.raises(MergeError) as err:
        merge_profiles(plain)
    msg = str(err.value)
    assert "sketch" in msg
    assert "part0" in msg  # which input lacks them
    assert "exact_only" in msg or "--exact-only" in msg
    for field in ("cardinality", "quantiles"):
        assert field in msg


def test_one_input_without_sketches_blocks_the_sketch_statistics(table):
    inputs = _profiles(_parts(table))
    inputs[2] = shape.profile(_parts(table)[2], name="part2")
    with pytest.raises(MergeError, match="part2"):
        merge_profiles(inputs)
    merged = merge_profiles(inputs, exact_only=True)
    assert merged.to_dict()["columns"]["id"]["cardinality"] is None


@pytest.mark.parametrize(
    "rows",
    [[0, 0], [1, 0], [0, 1], [1, 1], [1, 5], [0, 3, 0]],
)
def test_empty_and_single_row_partitions(table, rows):
    start, parts = 0, []
    for r in rows:
        parts.append(table.slice(start, r))
        start += r
    union = pa.concat_tables(parts)
    merged = merge_profiles(_profiles(parts), name="u").to_dict()
    expect = shape.profile(union, name="u").to_dict()
    assert merged["row_count"] == expect["row_count"] == sum(rows)
    for name, w in expect["columns"].items():
        c = merged["columns"][name]
        for f in EXACT_FIELDS:
            assert c[f] == w[f], (name, f, rows)
        for f in ("mean", "std"):
            if isinstance(w[f], float) and math.isfinite(w[f]):
                assert _close(c[f], w[f]), (name, f)
            else:
                assert c[f] == w[f], (name, f, rows)  # None, or the NaN of one value


def test_non_finite_values_propagate_like_the_whole(table):
    x = pa.table({"x": pa.array([1.0, float("nan"), 3.0, float("inf"), 5.0, -2.0, 8.0])})
    parts = [x.slice(0, 2), x.slice(2, 2), x.slice(4, 3)]
    merged = merge_profiles(_profiles(parts), name="x").to_dict()["columns"]["x"]
    whole = shape.profile(x, name="x").to_dict()["columns"]["x"]
    for f in ("mean", "std", "min_value", "max_value", "null_count"):
        a, b = merged[f], whole[f]
        same = a == b or (isinstance(a, float) and isinstance(b, float) and a != a and b != b)
        assert same, (f, a, b)


def test_dtype_promotion_and_conflicts():
    ints = pa.table({"v": pa.array([1, 2, 3])})
    floats = pa.table({"v": pa.array([1.5, 2.5])})
    exact = _profiles([ints, floats], sketches=False)
    both = merge_profiles(exact, exact_only=True, name="v").to_dict()["columns"]["v"]
    assert both["dtype"] == "float" and both["min_value"][1] == 1 and both["max_value"][1] == 3
    assert both["mean"] == pytest.approx(2.0)
    strings = pa.table({"v": pa.array(["a", "b"])})
    with pytest.raises(MergeError, match=r"column .v.: the types differ.*integer.*string"):
        merge_profiles(_profiles([ints, strings], sketches=False), exact_only=True)
    # sketches of different Arrow types cannot be combined, and the error says why
    with pytest.raises(MergeError, match=r"column types differ.*int64 vs double"):
        merge_profiles(_profiles([ints, floats]))


def test_structure_mismatches_are_errors(table):
    a = shape.profile(table.select(["id", "x"]), name="a", sketches=True)
    b = shape.profile(table.select(["id", "cat"]), name="b", sketches=True)
    with pytest.raises(MergeError, match="columns"):
        merge_profiles([a, b])
    ds = shape.profile({"t1": table.slice(0, 50), "t2": table.slice(0, 20)}, sketches=True)
    with pytest.raises(MergeError, match="tables"):
        merge_profiles([ds, a])
    ds_other = shape.profile({"t1": table.slice(0, 50)}, sketches=True)
    with pytest.raises(MergeError, match="tables"):
        merge_profiles([ds, ds_other])
    with pytest.raises(MergeError):
        merge_profiles([])


def test_dataset_merge(table):
    def day(lo, hi):
        return shape.profile(
            {"orders": table.slice(lo, hi - lo), "items": table.slice(lo, 3)}, sketches=True
        )

    merged = merge_profiles([day(0, 1000), day(1000, 2500), day(2500, 3000)], name="week")
    items = pa.concat_tables([table.slice(lo, 3) for lo in (0, 1000, 2500)])
    whole = shape.profile({"orders": table.slice(0, 3000), "items": items})
    assert merged.is_dataset
    assert set(merged.tables) == {"orders", "items"}
    assert merged.tables["orders"]["row_count"] == 3000
    assert merged.tables["items"]["row_count"] == 9
    for t in ("orders", "items"):
        for name, w in whole.tables[t]["columns"].items():
            c = merged.tables[t]["columns"][name]
            assert c["null_count"] == w["null_count"] and c["min_value"] == w["min_value"]
    assert set(merged.sketches["tables"]) == {"orders", "items"}


# ---------------------------------------------------------------- sketch statistics


def _true_rank(sorted_values: np.ndarray, v: float) -> tuple[float, float]:
    n = len(sorted_values)
    lo = np.searchsorted(sorted_values, v, side="left") / n
    hi = np.searchsorted(sorted_values, v, side="right") / n
    return float(lo), float(hi)


def test_merged_cardinality_within_the_hll_bound(sketched, table):
    c = _cols(merge_profiles(sketched))
    model = hll_error(14)
    tol = 4 * model.relative_error  # four standard errors
    for name, truth in (
        ("id", 6000),
        ("cat", 40),
        ("x", len(set(table["x"].drop_null().to_pylist()))),
    ):
        est = c[name]["cardinality"]
        assert isinstance(est, int)
        assert abs(est - truth) <= tol * truth + 1, (name, est, truth)
        assert c[name]["is_unique"] is None  # a sketch cannot prove uniqueness
    assert c["id"]["cardinality_ratio"] == pytest.approx(c["id"]["cardinality"] / 6000, abs=1e-6)


def test_merged_quantiles_within_the_kll_rank_bound(sketched, table):
    m = merge_profiles(sketched).to_dict()
    bound = kll_error(200).relative_error
    for name in ("id", "x"):
        values = np.sort(np.array(table[name].drop_null().to_pylist(), dtype=float))
        q = m["columns"][name]["quantiles"]
        assert q and set(q) >= {"p1", "p5", "p25", "p50", "p75", "p95", "p99"}
        for key, level in (("p1", 0.01), ("p25", 0.25), ("p50", 0.5), ("p75", 0.75), ("p99", 0.99)):
            lo, hi = _true_rank(values, q[key])
            rank_error = 0.0 if lo <= level <= hi else min(abs(lo - level), abs(hi - level))
            assert rank_error <= bound, (name, key, rank_error, bound)
    assert m["columns"]["cat"]["quantiles"] is None  # not a number column


def test_merged_top_values_respect_the_space_saving_bound(sketched, table):
    m = merge_profiles(sketched).to_dict()
    cap = space_saving_error(64).parameters["capacity"]
    truth: dict[str, int] = {}
    for w in table["cat"].to_pylist():
        truth[w] = truth.get(w, 0) + 1
    n = len(table)
    top = m["merge"]["sketch_columns"]["cat"]["top"]
    assert top and len(top) <= cap
    for value, count, err in top:
        assert count - err <= truth[value] <= count
        assert err <= n / cap
    # the heavy hitters (more than n/cap) are all present
    present = {v for v, _, _ in top}
    assert {w for w, c in truth.items() if c > n / cap} <= present
    assert top[0][0] == "w00"


def test_error_models_are_recorded(sketched):
    block = merge_profiles(sketched).to_dict()["merge"]["sketch_columns"]["x"]
    assert block["error_models"]["cardinality"]["algorithm"] == "hyperloglog"
    assert block["error_models"]["quantiles"]["algorithm"] == "kll-v2"
    cat = merge_profiles(sketched).to_dict()["merge"]["sketch_columns"]["cat"]
    assert cat["error_models"]["top"]["algorithm"] == "space-saving"


# ---------------------------------------------------------------- associativity


def test_merge_is_associative_and_order_free(table):
    a, b, c = _profiles(_parts(table, [0, 1000, 2500, 6000]))
    flat = merge_profiles([a, b, c]).to_dict()
    left = merge_profiles([merge_profiles([a, b]), c]).to_dict()
    right = merge_profiles([a, merge_profiles([b, c])]).to_dict()
    swapped = merge_profiles([c, a, b]).to_dict()
    bound = kll_error(200).relative_error
    for other in (left, right, swapped):
        assert other["row_count"] == flat["row_count"]
        for name, fc in flat["columns"].items():
            oc = other["columns"][name]
            for f in EXACT_FIELDS:
                assert oc[f] == fc[f], (name, f)
            if fc["mean"] is not None:
                assert _close(oc["mean"], fc["mean"]) and _close(oc["std"], fc["std"], 1e-9)
            assert oc["cardinality"] == fc["cardinality"], name  # HLL merge is exact
            if fc["quantiles"]:
                values = np.sort(np.array(table[name].drop_null().to_pylist(), dtype=float))
                for k, level in (
                    ("p1", 0.01),
                    ("p25", 0.25),
                    ("p50", 0.5),
                    ("p75", 0.75),
                    ("p99", 0.99),
                ):
                    # every grouping stays within the sketch's rank error of the true quantile
                    for q in (fc["quantiles"][k], oc["quantiles"][k]):
                        lo, hi = _true_rank(values, q)
                        gap = 0.0 if lo <= level <= hi else min(abs(lo - level), abs(hi - level))
                        assert gap <= bound, (name, k, gap)


def test_merged_profile_can_be_merged_again_and_keeps_sketches(table):
    parts = _profiles(_parts(table, [0, 2000, 6000]))
    ab = merge_profiles(parts)
    assert ab.sketches is not None
    again = merge_profiles([ab, shape.profile(table.slice(0, 100), name="e", sketches=True)])
    assert again.to_dict()["row_count"] == 6100


# ---------------------------------------------------------------- lineage


def test_merged_profiles_carry_their_inputs_content_ids(sketched):
    merged = merge_profiles(sketched, name="m")
    expect = [p.content_id for p in sketched]
    assert [i["shape_content_id"] for i in merged.merged_from] == expect
    assert [i["name"] for i in merged.merged_from] == [p.name for p in sketched]
    assert [i["row_count"] for i in merged.merged_from] == [
        sum(t["row_count"] for t in p.tables.values()) for p in sketched
    ]
    # the ids are part of the merged profile's body, so its own id changes with its inputs
    other = merge_profiles(sketched[1:], name="m")
    assert other.content_id != merged.content_id
    assert merge_profiles(sketched, name="m").content_id == merged.content_id  # deterministic
    # a merge of a merge names the merge it was given (the lineage stays walkable)
    top = merge_profiles([merged, sketched[0]])
    assert top.merged_from[0]["shape_content_id"] == merged.content_id
    assert "merged_from" not in shape.profile(pa.table({"a": [1]})).to_dict().get("merge", {})
    assert shape.profile(pa.table({"a": [1]})).merged_from == []


def test_merged_profile_saves_loads_and_reads_like_any_profile(sketched, tmp_path):
    merged = merge_profiles(sketched, name="m")
    cid = shape.save(merged, tmp_path / "m.shape")
    again = shape.load(tmp_path / "m.shape")
    assert again == merged and cid == again.content_id
    assert again.merged_from == merged.merged_from
    assert again.sketches == merged.sketches
    assert shape.diff(again, merged) is not None  # downstream readers accept the body
    assert "m" in again.to_html()


# ---------------------------------------------------------------- both kernels


_SCRIPT = """
import json, sys
import shape
from shape.profile import merge_profiles
from shape.kernel.dispatch import kernel_name
mode, out, *paths = sys.argv[1:]
if mode == "profile":
    import pyarrow as pa, pyarrow.parquet as pq
    t = pq.read_table(paths[0])
    cuts = [0, 0, 1, 1500, 1501, 3200, 6000]
    for i, (a, b) in enumerate(zip(cuts, cuts[1:])):
        shape.save(shape.profile(t.slice(a, b - a), name=f"part{i}", sketches=True),
                   f"{out}/part{i}.shape")
    print(kernel_name())
else:
    ps = [shape.load(p) for p in paths]
    m = merge_profiles(ps, name="m")
    json.dump({"body": m.to_dict(), "sketches": m.sketches, "id": m.content_id,
               "kernel": kernel_name()}, open(out, "w"), sort_keys=True, default=str)
"""


def _run(kernel: str, *args: str) -> str:
    env = {**os.environ, "SHAPE_KERNEL": kernel}
    done = subprocess.run(
        [sys.executable, "-c", _SCRIPT, *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def test_both_kernels_give_identical_merge_results(table, tmp_path):
    import pyarrow.parquet as pq

    pq.write_table(table, tmp_path / "t.parquet")
    docs = {}
    for build in ("rust", "python"):
        d = tmp_path / build
        d.mkdir()
        assert _run(build, "profile", str(d), str(tmp_path / "t.parquet")) == build
        for merge in ("rust", "python"):
            out = tmp_path / f"{build}-then-{merge}.json"
            files = [str(d / f"part{i}.shape") for i in range(len(CUTS) - 1)]
            _run(merge, "merge", str(out), *files)
            doc = json.loads(out.read_text())
            assert doc["kernel"] == merge
            docs[(build, merge)] = doc
    for build in ("rust", "python"):
        # the same inputs merged by either kernel: the same profile, sketch state and id
        a, b = docs[(build, "rust")], docs[(build, "python")]
        assert a["body"] == b["body"], build
        assert a["sketches"] == b["sketches"], build
        assert a["id"] == b["id"], build
    # inputs profiled by different kernels differ only in the last bits of float moments (and of
    # fitted parameters); what the sketches say is the same
    r, p = docs[("rust", "rust")]["body"], docs[("python", "python")]["body"]
    assert r["merge"]["sketch_columns"] == p["merge"]["sketch_columns"]
    for name, rc in r["columns"].items():
        pc = p["columns"][name]
        for f in ("cardinality", "quantiles", "null_count", "min_value", "max_value"):
            assert rc[f] == pc[f], (name, f)


# ---------------------------------------------------------------- CLI


def _cli(*argv: str) -> int:
    return cli_main(list(argv))


def test_cli_profile_sketches_and_merge(table, tmp_path, capsys):
    paths = []
    for i, part in enumerate(_parts(table)):
        src = tmp_path / f"p{i}.parquet"
        import pyarrow.parquet as pq

        pq.write_table(part, src)
        out = tmp_path / f"p{i}.shape"
        assert _cli("profile", str(src), "-o", str(out), "--sketches") == 0
        paths.append(str(out))
    capsys.readouterr()
    merged = tmp_path / "week.shape"
    assert _cli("profile", "merge", *paths, "-o", str(merged), "--name", "week") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["written"] == str(merged)
    prof = shape.load(merged)
    assert prof.name == "week"
    assert out["shape_content_id"] == prof.content_id
    assert [i["shape_content_id"] for i in out["merged_from"]] == [
        shape.load(p).content_id for p in paths
    ]
    assert prof.to_dict()["row_count"] == 6000
    assert prof.sketches is not None


def test_cli_merge_without_sketches_fails_clearly_and_exact_only_works(table, tmp_path, capsys):
    paths = []
    for i, part in enumerate(_parts(table, [0, 10, 20])):
        out = tmp_path / f"q{i}.shape"
        shape.save(shape.profile(part, name=f"q{i}"), out)
        paths.append(str(out))
    merged = tmp_path / "m.shape"
    assert _cli("profile", "merge", *paths, "-o", str(merged)) == 2
    err = capsys.readouterr().err
    assert "sketch" in err and "--exact-only" in err
    assert not merged.exists()
    assert _cli("profile", "merge", *paths, "-o", str(merged), "--exact-only") == 0
    assert shape.load(merged).to_dict()["row_count"] == 20


def test_cli_merge_needs_two_profiles_and_rejects_non_profiles(table, tmp_path, capsys):
    one = tmp_path / "one.shape"
    shape.save(shape.profile(table.slice(0, 5), name="one", sketches=True), one)
    assert _cli("profile", "merge", str(one), "-o", str(tmp_path / "x.shape")) == 2
    bogus = tmp_path / "nope.txt"
    bogus.write_text("hello")
    assert _cli("profile", "merge", str(one), str(bogus), "-o", str(tmp_path / "x.shape")) == 2
    assert "shape: error" in capsys.readouterr().err


# ---------------------------------------------------------------- 4. documentation


def test_error_bounds_are_documented_for_each_sketch():
    doc = (ROOT / "docs" / "PROFILE_MERGE.md").read_text(encoding="utf-8")
    hll, kll, ss = hll_error(14), kll_error(200), space_saving_error(64)
    assert "HyperLogLog" in doc and f"{hll.relative_error:.2%}" in doc
    assert "KLL" in doc and f"{kll.relative_error:.1%}" in doc and f"{kll.confidence:.0%}" in doc
    cap = ss.parameters["capacity"]
    assert "SpaceSaving" in doc and f"n / {cap}" in doc.replace("`", "")
    for needle in ("shape profile merge", "--sketches", "--exact-only", "sketches.json", "version"):
        assert needle in doc, needle
    for needle in ("exact", "cardinality", "quantiles", "content id"):
        assert needle in doc.lower(), needle
