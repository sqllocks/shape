"""W3-03 deliverables 1 and 2: ``shape.versions.bisect`` (git bisect for data)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from history_helpers import History, add_days, build_history, total_step, value_history

import shape
from shape.registry.local import LocalRegistry
from shape.versions import BisectResult, HistoryError, bisect


def bound(n: int) -> int:
    return math.ceil(math.log2(n)) + 2


@pytest.fixture(scope="module")
def steps(tmp_path_factory: pytest.TempPathFactory) -> History:
    """70 versions: ``v`` steps at version 35."""
    return value_history(tmp_path_factory.mktemp("steps"), 70, {"v": 35})


def ref(h: History, i: int) -> str:
    """The content id of version ``i`` (a registry ref)."""
    return LocalRegistry(h.registry).log(h.name)[i]["content_id"]


# ---- the planted step: day and column from the answer key ---------------------------------------


@pytest.mark.parametrize(
    ("good_day", "bad_day"), [(13, 14), (13, 15), (0, 30)], ids=["n=1", "n=2", "n=30"]
)
def test_names_the_planted_day_and_column(step_history: History, good_day: int, bad_day: int):
    event = step_history.event
    r = bisect(
        step_history.registry,
        step_history.name,
        good=ref(step_history, good_day),
        bad=ref(step_history, bad_day),
    )
    n = bad_day - good_day
    assert r.candidates == n
    assert r.found
    d = r.to_dict()
    assert d["first_bad"]["business_date"] == event["start"]
    assert {c["column"] for c in d["changes"]} == {event["column"]}
    assert {c["kind"] for c in d["changes"]} & set(event["detected_as"])
    assert d["last_good"]["business_date"] == add_days(event["start"], -1)
    assert r.evaluated <= bound(n)


def test_result_shape(step_history: History):
    r = bisect(
        step_history.registry,
        step_history.name,
        good=ref(step_history, 0),
        bad=ref(step_history, 30),
    )
    d = r.to_dict()
    assert d["format"] == "shape-bisect"
    assert d["version"] == 1
    assert d["name"] == "orders"
    assert d["mode"] == "bisect"
    assert d["found"] is True
    assert set(d["first_bad"]) == {"ref", "content_id", "business_date"}
    assert set(d["good"]) == {"ref", "content_id", "business_date"}
    assert d["first_bad"]["content_id"] == ref(step_history, 14)
    assert d["last_good"]["content_id"] == ref(step_history, 13)
    for c in d["changes"]:
        assert {"column", "kind", "before", "after"} <= set(c)
    assert d["max_evaluations"] == bound(30)
    assert d["evaluated"] == len(d["evaluations"]) <= d["max_evaluations"]
    assert d["warnings"] == []
    assert json.loads(json.dumps(d)) == d  # plain JSON
    assert isinstance(r, BisectResult)


def test_refs_may_be_tags_and_content_ids(step_history: History):
    reg = LocalRegistry(step_history.registry)
    reg.tag("orders", "before", ref(step_history, 3))
    reg.tag("orders", "after", ref(step_history, 25))
    r = bisect(step_history.registry, "orders", good="before", bad="after")
    assert r.to_dict()["first_bad"]["business_date"] == step_history.event["start"]
    assert r.to_dict()["good"]["ref"] == "before"
    r = bisect(step_history.registry, "orders", good=ref(step_history, 3), bad="latest")
    assert r.to_dict()["bad"]["ref"] == "latest"


# ---- the evaluation bound -----------------------------------------------------------------------


def test_never_evaluates_more_than_log2_plus_two(steps: History):
    runs = 0
    for n in (1, 2, 3, 4, 5, 8, 13, 30):
        for good_i in range(max(0, 35 - n), 35):
            r = bisect(steps.registry, "feed", good=ref(steps, good_i), bad=ref(steps, good_i + n))
            runs += 1
            assert r.candidates == n
            assert r.to_dict()["first_bad"]["business_date"] == steps.dates[35], (n, good_i)
            assert r.evaluated <= bound(n), (n, good_i, r.evaluated)
    assert runs > 50


def test_change_at_the_first_and_at_the_last_candidate(steps: History):
    first = bisect(steps.registry, "feed", good=ref(steps, 34), bad=ref(steps, 50))
    assert first.to_dict()["first_bad"]["content_id"] == ref(steps, 35)
    assert first.to_dict()["last_good"]["content_id"] == ref(steps, 34)  # the good version itself
    last = bisect(steps.registry, "feed", good=ref(steps, 20), bad=ref(steps, 35))
    assert last.to_dict()["first_bad"]["content_id"] == ref(steps, 35)
    assert last.to_dict()["bad"]["content_id"] == ref(steps, 35)


# ---- restricting the test -----------------------------------------------------------------------


@pytest.fixture(scope="module")
def two_columns(tmp_path_factory: pytest.TempPathFactory) -> History:
    """``v`` steps at version 10, ``w`` at version 20."""
    return value_history(tmp_path_factory.mktemp("two"), 30, {"v": 10, "w": 20})


def test_column_restricts_the_test(two_columns: History):
    h = two_columns
    both = bisect(h.registry, "feed", good=ref(h, 0), bad=ref(h, 29))
    assert both.to_dict()["first_bad"]["business_date"] == h.dates[10]
    only_w = bisect(h.registry, "feed", good=ref(h, 0), bad=ref(h, 29), column="w")
    assert only_w.to_dict()["first_bad"]["business_date"] == h.dates[20]
    assert {c["column"] for c in only_w.to_dict()["changes"]} == {"w"}
    assert only_w.to_dict()["test"]["column"] == "w"


def test_kind_restricts_the_test(two_columns: History):
    h = two_columns
    r = bisect(h.registry, "feed", good=ref(h, 0), bad=ref(h, 29), column="v", kind="mean_shift")
    assert {c["kind"] for c in r.to_dict()["changes"]} == {"mean_shift"}
    with pytest.raises(HistoryError, match=r"--bad .*tests good"):
        bisect(h.registry, "feed", good=ref(h, 0), bad=ref(h, 29), kind="null_rate_change")


def test_a_column_that_never_changes_is_a_bad_that_tests_good(two_columns: History):
    h = two_columns
    with pytest.raises(HistoryError, match=r"--bad .*tests good"):
        bisect(h.registry, "feed", good=ref(h, 0), bad=ref(h, 29), column="no_such_column")


# ---- the contract test --------------------------------------------------------------------------


def contract(tmp_path: Path, limit: float = 3.0) -> Path:
    p = tmp_path / "contract.json"
    p.write_text(json.dumps({"columns": {"v": {"max": limit}}}))
    return p


def test_contract_marks_a_version_bad_when_it_fails(steps: History, tmp_path: Path):
    c = contract(tmp_path)
    r = bisect(steps.registry, "feed", good=ref(steps, 0), bad=ref(steps, 69), contract=c)
    d = r.to_dict()
    assert d["first_bad"]["business_date"] == steps.dates[35]
    assert d["test"]["contract"] == str(c)
    assert d["violations"] and d["violations"][0]["column"] == "v"
    assert r.evaluated <= bound(69)  # includes the good version, which a contract may fail


def test_good_that_tests_bad_is_refused(steps: History, tmp_path: Path):
    with pytest.raises(HistoryError, match=r"--good .*tests bad"):
        bisect(
            steps.registry,
            "feed",
            good=ref(steps, 40),
            bad=ref(steps, 69),
            contract=contract(tmp_path),
        )


def test_bad_that_tests_good_is_refused(steps: History, tmp_path: Path):
    with pytest.raises(HistoryError, match=r"--bad .*tests good"):
        bisect(
            steps.registry,
            "feed",
            good=ref(steps, 0),
            bad=ref(steps, 20),
            contract=contract(tmp_path),
        )
    with pytest.raises(HistoryError, match=r"--bad .*tests good"):
        bisect(steps.registry, "feed", good=ref(steps, 0), bad=ref(steps, 20))


def test_kind_needs_the_diff_test(steps: History, tmp_path: Path):
    with pytest.raises(HistoryError, match="--kind"):
        bisect(
            steps.registry,
            "feed",
            good=ref(steps, 0),
            bad=ref(steps, 69),
            contract=contract(tmp_path),
            kind="mean_shift",
        )


# ---- versions that cannot be tested -------------------------------------------------------------


def test_a_share_safe_version_cannot_be_diffed(tmp_path: Path):
    from history_helpers import value_table

    from shape.privacy.safe_profile import to_safe_profile

    h = value_history(tmp_path, 3, {"v": 1})
    safe = to_safe_profile(shape.profile(value_table(0, {}), name="feed")).to_json()
    # dated like the good version and committed later, so it sorts right after it: the first
    # version a search over three candidates reads
    LocalRegistry(h.registry).commit("feed", safe, {"business_date": h.dates[0]})
    with pytest.raises(HistoryError, match="allow-raw") as e:
        bisect(h.registry, "feed", good=ref(h, 0), bad=ref(h, 2))
    assert "share-safe" in str(e.value)


# ---- refs and the registry ----------------------------------------------------------------------


def test_good_must_come_before_bad(steps: History):
    with pytest.raises(HistoryError, match="older"):
        bisect(steps.registry, "feed", good=ref(steps, 40), bad=ref(steps, 10))
    with pytest.raises(HistoryError, match="same version"):
        bisect(steps.registry, "feed", good=ref(steps, 40), bad=ref(steps, 40))


def test_unknown_names_refs_and_registries_are_errors(steps: History, tmp_path: Path):
    with pytest.raises(HistoryError, match="no versions of 'nope'"):
        bisect(steps.registry, "nope", good="a", bad="b")
    with pytest.raises(Exception, match="nope"):
        bisect(steps.registry, "feed", good="nope", bad="latest")
    missing = tmp_path / "missing"
    with pytest.raises(HistoryError, match="not a registry"):
        bisect(missing, "feed", good="a", bad="b")
    assert not missing.exists()  # reading a history never creates a registry


def test_versions_are_ordered_by_business_date_not_by_commit_order(tmp_path: Path):
    from history_helpers import commit_profile, value_table

    reg = LocalRegistry(tmp_path / "reg")
    order = [4, 0, 5, 2, 7, 1, 6, 3]  # committed out of order; the step is at day index 4
    for i in order:
        shifts = {"v": 5.0} if i >= 4 else {}
        commit_profile(reg, "feed", value_table(i, shifts), add_days("2026-05-01", i), tmp_path)
    r = bisect(tmp_path / "reg", "feed", good=_cid(reg, 0), bad=_cid(reg, 7))
    assert r.to_dict()["first_bad"]["business_date"] == "2026-05-05"
    assert r.to_dict()["last_good"]["business_date"] == "2026-05-04"


def _cid(reg: LocalRegistry, day: int) -> str:
    date = add_days("2026-05-01", day)
    for e in reg.log("feed"):
        if e["metadata"]["business_date"] == date:
            return str(e["content_id"])
    raise AssertionError(date)


def test_versions_without_a_business_date_are_ordered_by_commit_time(tmp_path: Path):
    from history_helpers import value_table

    import shape as s

    reg = LocalRegistry(tmp_path / "reg")
    cids = []
    for i in range(6):
        p = s.profile(value_table(i, {"v": 5.0} if i >= 3 else {}), name="feed")
        f = tmp_path / f"{i}.shape"
        s.save(p, f, capture="full")
        cids.append(reg.commit("feed", f.read_bytes(), allow_raw=True))
    r = bisect(tmp_path / "reg", "feed", good=cids[0], bad=cids[5])
    assert r.to_dict()["first_bad"]["content_id"] == cids[3]
    assert r.to_dict()["first_bad"]["business_date"] is None


# ---- the project file ---------------------------------------------------------------------------


def project_for(tmp_path: Path, h: History, body: str = "") -> Path:
    f = tmp_path / "shape.yml"
    f.write_text(
        "format: shape-project\nversion: 1\nsources:\n  feed:\n    path: data\n"
        f"    baseline:\n      kind: previous_run\n      registry: {h.registry}\n"
        f"{body}"
    )
    return f


def test_source_ignore_list_applies_and_flags_override(two_columns: History, tmp_path: Path):
    h = two_columns
    proj = project_for(tmp_path, h, "    ignore: [v]\n")
    # `v` is ignored by the source, so the first bad version is the one where `w` steps
    r = bisect(h.registry, "feed", good=ref(h, 0), bad=ref(h, 29), project=proj)
    assert r.to_dict()["first_bad"]["business_date"] == h.dates[20]
    assert r.to_dict()["test"]["source"] == "feed"
    # an explicit ignore list replaces the project's
    r = bisect(
        h.registry, "feed", good=ref(h, 0), bad=ref(h, 29), project=proj, ignore_columns=["w"]
    )
    assert r.to_dict()["first_bad"]["business_date"] == h.dates[10]
    # both columns ignored: nothing can change
    proj2 = project_for(tmp_path, h, "    ignore: [v, w]\n")
    with pytest.raises(HistoryError, match=r"--bad .*tests good"):
        bisect(h.registry, "feed", good=ref(h, 0), bad=ref(h, 29), project=proj2)


def test_source_is_found_by_the_name_of_the_history(two_columns: History, tmp_path: Path):
    h = two_columns
    f = tmp_path / "shape.yml"
    f.write_text(
        "format: shape-project\nversion: 1\nsources:\n"
        f"  other:\n    path: a\n    baseline:\n      kind: previous_run\n"
        f"      registry: {h.registry}\n"
        f"  feed:\n    path: b\n    ignore: [v]\n    baseline:\n      kind: previous_run\n"
        f"      registry: {h.registry}\n"
    )
    r = bisect(h.registry, "feed", good=ref(h, 0), bad=ref(h, 29), project=f)
    assert r.to_dict()["test"]["source"] == "feed"
    assert r.to_dict()["first_bad"]["business_date"] == h.dates[20]
    r = bisect(h.registry, "feed", good=ref(h, 0), bad=ref(h, 29), project=f, source="other")
    assert r.to_dict()["first_bad"]["business_date"] == h.dates[10]
    with pytest.raises(Exception, match="no source 'zzz'"):
        bisect(h.registry, "feed", good=ref(h, 0), bad=ref(h, 29), project=f, source="zzz")


# ---- --verify-all -------------------------------------------------------------------------------


def test_verify_all_flags_a_gap_inside_the_range(tmp_path: Path):
    from history_helpers import commit_profile, value_table

    reg = LocalRegistry(tmp_path / "reg")
    bad_at = {4, 5, 6, 9, 10, 11}  # good again at 7 and 8, bad again from 9
    for i in range(12):
        shifts = {"v": 5.0} if i in bad_at else {}
        commit_profile(reg, "feed", value_table(i, shifts), add_days("2026-05-01", i), tmp_path)
    log = reg.log("feed")
    r = bisect(
        tmp_path / "reg",
        "feed",
        good=log[0]["content_id"],
        bad=log[11]["content_id"],
        verify_all=True,
    )
    d = r.to_dict()
    assert d["first_bad"]["business_date"] == "2026-05-05"
    assert [f["business_date"] for f in d["flips"]] == ["2026-05-08", "2026-05-09"]
    assert any("assumes the change persists" in w for w in d["warnings"])
    assert d["evaluated"] == 11  # every candidate
    # a plain bisect on this history is not told about the gap
    plain = bisect(tmp_path / "reg", "feed", good=log[0]["content_id"], bad=log[11]["content_id"])
    assert plain.to_dict()["flips"] == []


def test_verify_all_agrees_with_bisect_on_a_monotone_history(steps: History):
    a = bisect(steps.registry, "feed", good=ref(steps, 5), bad=ref(steps, 60))
    b = bisect(steps.registry, "feed", good=ref(steps, 5), bad=ref(steps, 60), verify_all=True)
    assert a.to_dict()["first_bad"] == b.to_dict()["first_bad"]
    assert b.to_dict()["flips"] == [] and b.to_dict()["warnings"] == []
    assert b.evaluated == 55 > a.evaluated


# ---- --coarse -----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def long_history(tmp_path_factory: pytest.TempPathFactory) -> History:
    """120 versions with sketch state; ``v`` steps at version 71 (a Wednesday-ish mid-week)."""
    return value_history(tmp_path_factory.mktemp("long"), 120, {"v": 71})


@pytest.mark.parametrize("coarse", ["week", "month"])
def test_coarse_finds_the_same_version_as_a_plain_bisect(long_history: History, coarse: str):
    h = long_history
    plain = bisect(h.registry, "feed", good=ref(h, 0), bad=ref(h, 119))
    c = bisect(h.registry, "feed", good=ref(h, 0), bad=ref(h, 119), coarse=coarse)
    assert c.to_dict()["first_bad"] == plain.to_dict()["first_bad"]
    assert c.to_dict()["last_good"] == plain.to_dict()["last_good"]
    assert c.to_dict()["mode"] == f"coarse-{coarse}"
    assert c.to_dict()["first_bad"]["business_date"] == h.dates[71]


def test_coarse_reads_fewer_full_profiles_for_the_final_search(long_history: History):
    h = long_history
    plain = bisect(h.registry, "feed", good=ref(h, 0), bad=ref(h, 119))
    c = bisect(h.registry, "feed", good=ref(h, 0), bad=ref(h, 119), coarse="week")
    cost = c.to_dict()["cost"]
    assert cost["window_tests"] >= 1
    assert cost["full_profile_tests"] < plain.to_dict()["cost"]["full_profile_tests"]
    assert plain.to_dict()["cost"]["window_tests"] == 0


@pytest.mark.parametrize("position", [0, 1, 2, 3, 4, 5, 6])  # every weekday of the step
def test_coarse_matches_plain_wherever_the_step_falls_in_a_window(tmp_path: Path, position: int):
    # 2026-03-01 is a Sunday; the ISO week of version i starts at i = 1 + 7k
    k = 29 + position
    h = value_history(tmp_path, 60, {"v": k})
    plain = bisect(h.registry, "feed", good=ref(h, 0), bad=ref(h, 59))
    c = bisect(h.registry, "feed", good=ref(h, 0), bad=ref(h, 59), coarse="week")
    assert plain.to_dict()["first_bad"]["business_date"] == h.dates[k]
    assert c.to_dict()["first_bad"] == plain.to_dict()["first_bad"]


def test_coarse_needs_sketch_state(tmp_path: Path):
    h = value_history(tmp_path, 30, {"v": 15}, sketches=False)
    with pytest.raises(HistoryError, match=r"--sketches"):
        bisect(h.registry, "feed", good=ref(h, 0), bad=ref(h, 29), coarse="week")
    # a plain bisect does not need it
    assert bisect(h.registry, "feed", good=ref(h, 0), bad=ref(h, 29)).found


def test_coarse_ignores_the_row_count_of_a_merged_window(long_history: History):
    # a window of seven merged days holds seven days of rows; that is not a row-count change, so
    # a window before the step tests good
    h = long_history
    c = bisect(h.registry, "feed", good=ref(h, 0), bad=ref(h, 119), coarse="week")
    windows = [e for e in c.to_dict()["evaluations"] if "window" in e]
    assert windows[0]["bad"] is False and windows[0]["versions"] == 7


def test_coarse_rejects_other_windows(steps: History, tmp_path: Path):
    with pytest.raises(HistoryError, match="diff test only"):
        bisect(
            steps.registry,
            "feed",
            good=ref(steps, 0),
            bad=ref(steps, 69),
            coarse="week",
            contract=contract(tmp_path),
        )
    with pytest.raises(HistoryError, match="week or month"):
        bisect(steps.registry, "feed", good=ref(steps, 0), bad=ref(steps, 69), coarse="year")
    with pytest.raises(HistoryError, match="--verify-all"):
        bisect(
            steps.registry,
            "feed",
            good=ref(steps, 0),
            bad=ref(steps, 69),
            coarse="week",
            verify_all=True,
        )


def test_the_build_history_helper_plants_what_the_answer_key_says(tmp_path: Path):
    # guard for the acceptance test above: the answer key and the helper's days agree
    h = build_history(tmp_path, 4, [total_step(2)])
    assert h.event["start"] == h.dates[2]
