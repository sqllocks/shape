"""W1-04: baseline kinds resolve against the existing registry (``shape registry``)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from shape.project import ProjectError, parse_project, resolve_baseline
from shape.registry.local import LocalRegistry

REG = "shapes/registry"


def project(tmp_path: Path, baseline: str):
    text = (
        "format: shape-project\nversion: 1\nsources:\n  orders:\n    path: data\n"
        f"    baseline:\n      registry: {REG}\n{baseline}"
    )
    return parse_project(text, tmp_path / "shape.yml")


def registry(tmp_path: Path, days: list[str], name: str = "orders") -> LocalRegistry:
    reg = LocalRegistry(tmp_path / REG)
    for day in days:
        reg.commit(name, f"profile of {day}".encode(), {"business_date": day})
    return reg


def contents(resolved) -> list[str]:
    return [Path(e.path).read_bytes().decode() for e in resolved.entries]


def test_previous_run_is_the_newest_entry(tmp_path: Path):
    registry(tmp_path, ["2026-09-28", "2026-10-01", "2026-09-30"])
    p = project(tmp_path, "      kind: previous_run\n")
    r = resolve_baseline(p, "orders", workdir=tmp_path / "w")
    assert r.kind == "previous_run"
    assert contents(r) == ["profile of 2026-09-30"]  # newest by commit order, not by date
    assert r.entries[0].content_id


def test_previous_run_respects_a_cutoff_date(tmp_path: Path):
    registry(tmp_path, ["2026-09-28", "2026-09-30", "2026-10-02"])
    p = project(tmp_path, "      kind: previous_run\n")
    r = resolve_baseline(p, "orders", as_of=date(2026, 10, 1), workdir=tmp_path / "w")
    assert contents(r) == ["profile of 2026-09-30"]


def test_empty_registry_is_a_clear_error(tmp_path: Path):
    p = project(tmp_path, "      kind: previous_run\n")
    with pytest.raises(ProjectError, match=r"orders.*no entries.*shapes/registry"):
        resolve_baseline(p, "orders", workdir=tmp_path / "w")


def test_same_weekday_picks_the_newest_earlier_same_weekday(tmp_path: Path):
    # 2026-10-03 is a Saturday
    registry(tmp_path, ["2026-09-19", "2026-09-26", "2026-09-27", "2026-10-03", "2026-10-01"])
    p = project(tmp_path, "      kind: same_weekday\n")
    r = resolve_baseline(p, "orders", as_of=date(2026, 10, 3), workdir=tmp_path / "w")
    assert contents(r) == ["profile of 2026-09-26"]  # not the same day, not Sunday


def test_same_weekday_without_a_match_names_the_weekday(tmp_path: Path):
    registry(tmp_path, ["2026-10-01", "2026-10-02"])
    p = project(tmp_path, "      kind: same_weekday\n")
    with pytest.raises(ProjectError, match=r"Saturday.*2026-10-03"):
        resolve_baseline(p, "orders", as_of=date(2026, 10, 3), workdir=tmp_path / "w")


def test_rolling_window_is_the_n_newest_entries_before_the_date(tmp_path: Path):
    registry(tmp_path, [f"2026-09-{d:02d}" for d in range(20, 31)] + ["2026-10-03"])
    p = project(tmp_path, "      kind: rolling_window\n      window: 3\n")
    r = resolve_baseline(p, "orders", as_of=date(2026, 10, 3), workdir=tmp_path / "w")
    assert contents(r) == [
        "profile of 2026-09-30",
        "profile of 2026-09-29",
        "profile of 2026-09-28",
    ]


def test_rolling_window_uses_what_exists_when_underfilled(tmp_path: Path):
    registry(tmp_path, ["2026-09-30"])
    p = project(tmp_path, "      kind: rolling_window\n      window: 7\n")
    r = resolve_baseline(p, "orders", as_of=date(2026, 10, 3), workdir=tmp_path / "w")
    assert len(r.entries) == 1 and r.window == 7


def test_rolling_window_with_nothing_before_the_date_is_an_error(tmp_path: Path):
    registry(tmp_path, ["2026-10-03"])
    p = project(tmp_path, "      kind: rolling_window\n      window: 2\n")
    with pytest.raises(ProjectError, match="before 2026-10-03"):
        resolve_baseline(p, "orders", as_of=date(2026, 10, 3), workdir=tmp_path / "w")


def test_month_end_is_the_last_day_of_the_previous_month(tmp_path: Path):
    registry(tmp_path, ["2026-09-29", "2026-09-30", "2026-10-01"])
    p = project(tmp_path, "      kind: month_end\n")
    r = resolve_baseline(p, "orders", as_of=date(2026, 10, 3), workdir=tmp_path / "w")
    assert contents(r) == ["profile of 2026-09-30"]


def test_month_end_falls_back_inside_that_month_only(tmp_path: Path):
    registry(tmp_path, ["2026-09-28", "2026-08-31"])
    p = project(tmp_path, "      kind: month_end\n")
    r = resolve_baseline(p, "orders", as_of=date(2026, 10, 3), workdir=tmp_path / "w")
    assert contents(r) == ["profile of 2026-09-28"]
    only_old = tmp_path / "other"
    only_old.mkdir()
    registry(only_old, ["2026-08-31"])
    p2 = project(only_old, "      kind: month_end\n")
    with pytest.raises(ProjectError, match="2026-09"):
        resolve_baseline(p2, "orders", as_of=date(2026, 10, 3), workdir=only_old / "w")


def test_month_end_across_a_year_boundary(tmp_path: Path):
    registry(tmp_path, ["2025-12-31"])
    p = project(tmp_path, "      kind: month_end\n")
    r = resolve_baseline(p, "orders", as_of=date(2026, 1, 15), workdir=tmp_path / "w")
    assert contents(r) == ["profile of 2025-12-31"]


def test_the_same_day_twice_takes_the_newest_commit(tmp_path: Path):
    reg = registry(tmp_path, ["2026-09-30"])
    reg.commit("orders", b"second run", {"business_date": "2026-09-30"})
    p = project(tmp_path, "      kind: month_end\n")
    r = resolve_baseline(p, "orders", as_of=date(2026, 10, 3), workdir=tmp_path / "w")
    assert contents(r) == ["second run"]


def test_entries_without_a_business_date_use_the_commit_date(tmp_path: Path):
    reg = LocalRegistry(tmp_path / REG)
    reg.commit("orders", b"now")
    p = project(tmp_path, "      kind: same_weekday\n")
    today = date.today()
    with pytest.raises(ProjectError):  # committed today: not before today
        resolve_baseline(p, "orders", as_of=today, workdir=tmp_path / "w")
    r = resolve_baseline(
        p, "orders", as_of=date.fromordinal(today.toordinal() + 7), workdir=tmp_path / "w"
    )
    assert contents(r) == ["now"]


def test_registry_name_defaults_to_the_source_and_can_be_set(tmp_path: Path):
    registry(tmp_path, ["2026-09-30"], name="daily")
    p = project(tmp_path, "      kind: previous_run\n      name: daily\n")
    assert contents(resolve_baseline(p, "orders", workdir=tmp_path / "w")) == [
        "profile of 2026-09-30"
    ]


def test_pinned_artifact_is_a_file_relative_to_the_project(tmp_path: Path):
    (tmp_path / "baselines").mkdir()
    (tmp_path / "baselines" / "good.shape").write_bytes(b"pinned bytes")
    p = project(tmp_path, "      kind: pinned\n      artifact: baselines/good.shape\n")
    r = resolve_baseline(p, "orders", workdir=tmp_path / "w")
    assert r.kind == "pinned"
    assert Path(r.entries[0].path) == tmp_path / "baselines" / "good.shape"


def test_pinned_artifact_missing(tmp_path: Path):
    p = project(tmp_path, "      kind: pinned\n      artifact: baselines/none.shape\n")
    with pytest.raises(ProjectError, match=r"pinned artifact.*none\.shape"):
        resolve_baseline(p, "orders", workdir=tmp_path / "w")


def test_pinned_ref_resolves_a_tag_ref_or_content_id(tmp_path: Path):
    reg = registry(tmp_path, ["2026-09-28", "2026-09-30"])
    first = reg.log("orders")[0]["content_id"]
    reg.tag("orders", "production", first)
    p = project(tmp_path, "      kind: pinned\n      ref: production\n")
    assert contents(resolve_baseline(p, "orders", workdir=tmp_path / "w")) == [
        "profile of 2026-09-28"
    ]
    p2 = project(tmp_path, f"      kind: pinned\n      ref: {first}\n")
    assert contents(resolve_baseline(p2, "orders", workdir=tmp_path / "w2")) == [
        "profile of 2026-09-28"
    ]


def test_pinned_unknown_ref(tmp_path: Path):
    registry(tmp_path, ["2026-09-28"])
    p = project(tmp_path, "      kind: pinned\n      ref: nope\n")
    with pytest.raises(ProjectError, match=r"orders@nope"):
        resolve_baseline(p, "orders", workdir=tmp_path / "w")


def test_no_baseline_declared(tmp_path: Path):
    p = parse_project(
        "format: shape-project\nversion: 1\nsources:\n  orders:\n    path: d\n",
        tmp_path / "shape.yml",
    )
    with pytest.raises(ProjectError, match="declares no baseline"):
        resolve_baseline(p, "orders", workdir=tmp_path / "w")


def test_resolving_never_creates_the_registry(tmp_path: Path):
    p = project(tmp_path, "      kind: previous_run\n")
    with pytest.raises(ProjectError):
        resolve_baseline(p, "orders", workdir=tmp_path / "w")
    assert not (tmp_path / REG).exists()
