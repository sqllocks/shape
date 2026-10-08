"""W8-03: ``LocalRegistry.prune``: what is removed, what is kept and why, the report, dry run,
crash safety and the registry lock (issue #566, Wanted 1 to 4)."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from prune_helpers import (
    T0,
    Clock,
    assert_nothing_dangles,
    commit_at,
    day,
    object_ids,
    snapshot,
)

import shape.registry.local as local_mod
from shape.registry import LocalRegistry, RegistryError

DATA = Path(__file__).parent / "data"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def four_days(clock: Clock, root: Path, name: str = "orders") -> LocalRegistry:
    """``name`` committed on days 0, 1, 2 and 3 (contents ``a``, ``b``, ``c``, ``d``)."""
    reg = LocalRegistry(root)
    for n, data in enumerate((b"a", b"b", b"c", b"d")):
        commit_at(clock, reg, name, data, day(n))
    return reg


# ---- Wanted 1: the API, what is removed and what is kept ---------------------------------------


def test_removes_entries_strictly_before_the_cutoff(clock: Clock, tmp_path: Path) -> None:
    reg = four_days(clock, tmp_path / "reg")
    report = reg.prune(day(2))
    assert [e["content_id"] for e in reg.log("orders")] == [sha(b"c"), sha(b"d")]
    assert report["names"]["orders"]["entries_removed"] == 2
    assert report["names"]["orders"]["entries_kept"] == 2
    assert object_ids(tmp_path / "reg") == {sha(b"c"), sha(b"d")}
    assert_nothing_dangles(reg)


def test_a_cutoff_equal_to_created_at_keeps_the_entry(clock: Clock, tmp_path: Path) -> None:
    reg = four_days(clock, tmp_path / "reg")
    created = reg.log("orders")[1]["created_at"]
    assert created == day(1).timestamp()
    reg.prune(datetime.fromtimestamp(created, UTC))
    assert [e["content_id"] for e in reg.log("orders")] == [sha(b"b"), sha(b"c"), sha(b"d")]
    # a microsecond later, it goes
    reg.prune(day(1) + timedelta(microseconds=1))
    assert [e["content_id"] for e in reg.log("orders")] == [sha(b"c"), sha(b"d")]


def test_a_cutoff_before_every_entry_removes_nothing(clock: Clock, tmp_path: Path) -> None:
    reg = four_days(clock, tmp_path / "reg")
    before = snapshot(tmp_path / "reg")
    report = reg.prune(T0)
    assert report["names"]["orders"]["entries_removed"] == 0
    assert report["objects_removed"] == [] and report["bytes_freed"] == 0
    assert snapshot(tmp_path / "reg") == before


def test_keep_last_keeps_the_newest_entries_whatever_their_age(
    clock: Clock, tmp_path: Path
) -> None:
    reg = four_days(clock, tmp_path / "reg")
    report = reg.prune(day(10), keep_last=3)
    assert [e["content_id"] for e in reg.log("orders")] == [sha(b"b"), sha(b"c"), sha(b"d")]
    kept = report["names"]["orders"]["kept_because"]
    assert kept["keep_last"] == [sha(b"b"), sha(b"c"), sha(b"d")]
    # keep_last larger than the log keeps everything
    assert reg.prune(day(10), keep_last=99)["names"]["orders"]["entries_removed"] == 0


def test_the_default_keep_last_is_one(clock: Clock, tmp_path: Path) -> None:
    reg = four_days(clock, tmp_path / "reg")
    report = reg.prune(day(10))
    assert [e["content_id"] for e in reg.log("orders")] == [sha(b"d")]
    assert report["names"]["orders"]["kept_because"]["keep_last"] == [sha(b"d")]


@pytest.mark.parametrize("bad", [0, -1, -100])
def test_keep_last_below_one_is_refused(clock: Clock, tmp_path: Path, bad: int) -> None:
    reg = four_days(clock, tmp_path / "reg")
    before = snapshot(tmp_path / "reg")
    with pytest.raises(ValueError, match="keep_last"):
        reg.prune(day(10), keep_last=bad)
    assert snapshot(tmp_path / "reg") == before


@pytest.mark.parametrize("bad", [True, 1.5, "2", None])
def test_keep_last_must_be_an_integer(clock: Clock, tmp_path: Path, bad: Any) -> None:
    reg = four_days(clock, tmp_path / "reg")
    with pytest.raises(TypeError, match="keep_last"):
        reg.prune(day(10), keep_last=bad)


def test_a_naive_datetime_is_refused(clock: Clock, tmp_path: Path) -> None:
    reg = four_days(clock, tmp_path / "reg")
    before = snapshot(tmp_path / "reg")
    with pytest.raises(ValueError, match="timezone"):
        reg.prune(datetime(2026, 6, 3))
    assert snapshot(tmp_path / "reg") == before


@pytest.mark.parametrize(
    ("text", "cutoff"),
    [
        ("2026-06-03T00:00:00Z", "2026-06-03T00:00:00Z"),
        ("2026-06-03T00:00:00+00:00", "2026-06-03T00:00:00Z"),
        ("2026-06-03T02:00:00+02:00", "2026-06-03T00:00:00Z"),
        ("2026-06-03", "2026-06-03T00:00:00Z"),
        ("2026-06-02T23:59:59.5Z", "2026-06-02T23:59:59.500000Z"),
    ],
)
def test_an_iso_string_is_read_as_utc(clock: Clock, tmp_path: Path, text: str, cutoff: str) -> None:
    reg = four_days(clock, tmp_path / "reg")
    report = reg.prune(text, dry_run=True)
    assert report["cutoff"] == cutoff
    assert report["names"]["orders"]["entries_removed"] == 2


def test_an_aware_datetime_in_another_zone_is_converted(clock: Clock, tmp_path: Path) -> None:
    reg = four_days(clock, tmp_path / "reg")
    plus2 = datetime(2026, 6, 3, 2, tzinfo=timezone(timedelta(hours=2)))
    assert reg.prune(plus2, dry_run=True)["cutoff"] == "2026-06-03T00:00:00Z"


@pytest.mark.parametrize(
    "bad", ["2026-06-03T00:00:00", "yesterday", "", "2026-13-01", "2026-06-03T25:00:00Z"]
)
def test_a_bad_or_zoneless_string_is_refused(clock: Clock, tmp_path: Path, bad: str) -> None:
    reg = four_days(clock, tmp_path / "reg")
    with pytest.raises(ValueError):
        reg.prune(bad)


def test_a_cutoff_of_another_type_is_refused(clock: Clock, tmp_path: Path) -> None:
    reg = four_days(clock, tmp_path / "reg")
    with pytest.raises(TypeError):
        reg.prune(day(1).timestamp())  # type: ignore[arg-type]


def test_names_limits_the_prune(clock: Clock, tmp_path: Path) -> None:
    reg = four_days(clock, tmp_path / "reg")
    for n, data in enumerate((b"p", b"q")):
        commit_at(clock, reg, "people", data, day(n))
    report = reg.prune(day(10), names=["people"])
    assert set(report["names"]) == {"people"}
    assert len(reg.log("orders")) == 4 and len(reg.log("people")) == 1
    assert sha(b"a") in object_ids(tmp_path / "reg") and sha(b"p") not in object_ids(
        tmp_path / "reg"
    )


def test_every_name_by_default(clock: Clock, tmp_path: Path) -> None:
    reg = four_days(clock, tmp_path / "reg")
    commit_at(clock, reg, "people", b"p", day(0))
    commit_at(clock, reg, "people", b"q", day(1))
    assert set(reg.prune(day(10))["names"]) == {"orders", "people"}


def test_an_unknown_name_is_refused(clock: Clock, tmp_path: Path) -> None:
    reg = four_days(clock, tmp_path / "reg")
    before = snapshot(tmp_path / "reg")
    with pytest.raises(RegistryError, match="nope"):
        reg.prune(day(10), names=["orders", "nope"])
    with pytest.raises(RegistryError):
        reg.prune(day(10), names=["../logs/orders"])
    assert snapshot(tmp_path / "reg") == before


def test_an_empty_names_list_is_refused(clock: Clock, tmp_path: Path) -> None:
    reg = four_days(clock, tmp_path / "reg")
    with pytest.raises(ValueError, match="names"):
        reg.prune(day(10), names=[])


def test_refs_and_tags_keep_their_entries(clock: Clock, tmp_path: Path) -> None:
    reg = LocalRegistry(tmp_path / "reg")
    a = commit_at(clock, reg, "orders", b"a", day(0))
    b = commit_at(clock, reg, "orders", b"b", day(1))
    commit_at(clock, reg, "orders", b"x", day(2))
    c = commit_at(clock, reg, "orders", b"c", day(3))
    reg.tag("orders", "v1", a)
    reg.promote("orders", b, "production")
    report = reg.prune(day(10))
    assert [e["content_id"] for e in reg.log("orders")] == [a, b, c]
    assert report["names"]["orders"] == {
        "entries_removed": 1,
        "entries_kept": 3,
        "kept_because": {"ref": [b, c], "tag": [a], "keep_last": [c]},
    }
    assert report["objects_removed"] == [sha(b"x")]
    assert_nothing_dangles(reg)


def test_a_name_with_only_tagged_entries_loses_nothing(clock: Clock, tmp_path: Path) -> None:
    reg = LocalRegistry(tmp_path / "reg")
    ids = [commit_at(clock, reg, "orders", d, day(n)) for n, d in enumerate((b"a", b"b", b"c"))]
    for n, cid in enumerate(ids):
        reg.tag("orders", f"v{n}", cid)
    before = snapshot(tmp_path / "reg")
    report = reg.prune(day(10))
    assert report["names"]["orders"]["entries_removed"] == 0
    assert report["names"]["orders"]["kept_because"]["tag"] == ids
    assert report["objects_removed"] == []
    assert snapshot(tmp_path / "reg") == before


def test_every_entry_of_a_referenced_content_id_is_kept(clock: Clock, tmp_path: Path) -> None:
    """The same bytes committed twice: a tag on that content id keeps both entries."""
    reg = LocalRegistry(tmp_path / "reg")
    a = commit_at(clock, reg, "orders", b"a", day(0))
    commit_at(clock, reg, "orders", b"b", day(1))
    commit_at(clock, reg, "orders", b"a", day(2))
    d = commit_at(clock, reg, "orders", b"d", day(3))
    reg.tag("orders", "first", a)
    reg.prune(day(10))
    assert [e["content_id"] for e in reg.log("orders")] == [a, a, d]


def test_a_ref_written_by_an_older_shape_is_honoured(clock: Clock, tmp_path: Path) -> None:
    """A ref file with a trailing newline (hand-written or older) still keeps its entry."""
    reg = four_days(clock, tmp_path / "reg")
    (tmp_path / "reg" / "refs" / "orders" / "old").write_text(sha(b"a") + "\n")
    reg.prune(day(10))
    assert [e["content_id"] for e in reg.log("orders")] == [sha(b"a"), sha(b"d")]
    assert_nothing_dangles(reg)


# ---- Wanted 2: objects ---------------------------------------------------------------------------


def test_an_object_shared_by_two_names_survives_while_one_references_it(
    clock: Clock, tmp_path: Path
) -> None:
    root = tmp_path / "reg"
    reg = LocalRegistry(root)
    shared = commit_at(clock, reg, "x", b"shared", day(0))
    commit_at(clock, reg, "y", b"shared", day(0))
    commit_at(clock, reg, "x", b"x2", day(1))
    commit_at(clock, reg, "y", b"y2", day(1))
    report = reg.prune(day(10), names=["x"])
    assert report["objects_removed"] == []
    assert shared in object_ids(root)
    assert reg.checkout("y", shared) == b"shared"
    report = reg.prune(day(10), names=["y"])
    assert report["objects_removed"] == [shared]
    assert report["bytes_freed"] == len(b"shared")
    assert shared not in object_ids(root)
    assert_nothing_dangles(reg)


def test_an_object_a_ref_of_another_name_points_at_survives(clock: Clock, tmp_path: Path) -> None:
    root = tmp_path / "reg"
    reg = LocalRegistry(root)
    shared = commit_at(clock, reg, "x", b"shared", day(0))
    commit_at(clock, reg, "x", b"x2", day(1))
    # a ref of name z, which has no log of its own, points at the same bytes
    (root / "refs" / "z").mkdir()
    (root / "refs" / "z" / "pinned").write_text(shared)
    report = reg.prune(day(10))
    assert report["objects_removed"] == [] and shared in object_ids(root)


def test_an_object_still_in_a_newer_entry_survives(clock: Clock, tmp_path: Path) -> None:
    reg = LocalRegistry(tmp_path / "reg")
    a = commit_at(clock, reg, "orders", b"a", day(0))
    commit_at(clock, reg, "orders", b"b", day(1))
    commit_at(clock, reg, "orders", b"a", day(5))
    report = reg.prune(day(3))
    # latest points at ``a``: every entry of that content id is kept, the old one too
    assert [e["content_id"] for e in reg.log("orders")] == [a, a]
    assert report["objects_removed"] == [sha(b"b")]
    assert reg.checkout("orders", a) == b"a"


def test_an_object_nothing_points_at_is_removed(clock: Clock, tmp_path: Path) -> None:
    root = tmp_path / "reg"
    reg = four_days(clock, root)
    orphan = sha(b"orphan")
    (root / "objects" / orphan).write_bytes(b"orphan")
    report = reg.prune(T0)  # removes no entry
    assert report["objects_removed"] == [orphan]
    assert report["bytes_freed"] == len(b"orphan")
    assert not (root / "objects" / orphan).exists()


def test_files_that_are_not_content_ids_are_skipped(clock: Clock, tmp_path: Path) -> None:
    root = tmp_path / "reg"
    reg = four_days(clock, root)
    objects = root / "objects"
    strays = {
        "README.txt": b"notes",
        ".tmp-abc123": b"half written",
        sha(b"upper").upper(): b"upper",
        sha(b"short")[:63]: b"short",
        sha(b"long") + "0": b"long",
    }
    for name, data in strays.items():
        (objects / name).write_bytes(data)
    (objects / "sub").mkdir()
    (objects / "sub" / sha(b"nested")).write_bytes(b"nested")
    report = reg.prune(day(10))
    assert {s["path"] for s in report["skipped"]} == {
        *(f"objects/{n}" for n in strays),
        "objects/sub",
    }
    assert all(s["reason"] for s in report["skipped"])
    for name, data in strays.items():
        assert (objects / name).read_bytes() == data
    assert (objects / "sub" / sha(b"nested")).read_bytes() == b"nested"
    assert set(report["objects_removed"]) == {sha(b"a"), sha(b"b"), sha(b"c")}


def test_bytes_freed_is_the_size_of_the_removed_objects(clock: Clock, tmp_path: Path) -> None:
    reg = LocalRegistry(tmp_path / "reg")
    for n, size in enumerate((10, 200, 3000, 7)):
        commit_at(clock, reg, "orders", bytes([n]) * size, day(n))
    assert reg.prune(day(10))["bytes_freed"] == 10 + 200 + 3000


def test_a_log_line_that_cannot_be_read_is_kept_and_reported(clock: Clock, tmp_path: Path) -> None:
    root = tmp_path / "reg"
    reg = four_days(clock, root)
    log = root / "logs" / "orders.jsonl"
    lines = log.read_text().splitlines()
    no_time = json.dumps({"name": "orders", "content_id": sha(b"a"), "metadata": {}})
    log.write_text("\n".join([lines[0], no_time, "{not json", *lines[1:]]) + "\n")
    report = reg.prune(day(10))
    kept = log.read_text().splitlines()
    assert kept == [no_time, "{not json", lines[3]]
    assert {s["path"] for s in report["skipped"]} == {
        "logs/orders.jsonl:2",
        "logs/orders.jsonl:3",
    }
    assert sha(b"a") in object_ids(root)  # the kept entry still points at it


# ---- Wanted 3: the report and dry run -----------------------------------------------------------


def test_the_report(clock: Clock, tmp_path: Path) -> None:
    reg = four_days(clock, tmp_path / "reg")
    report = reg.prune("2026-06-03T00:00:00Z")
    assert report == {
        "format": "shape-registry-prune",
        "version": 1,
        "cutoff": "2026-06-03T00:00:00Z",
        "dry_run": False,
        "names": {
            "orders": {
                "entries_removed": 2,
                "entries_kept": 2,
                "kept_because": {"ref": [], "tag": [], "keep_last": []},
            }
        },
        "objects_removed": sorted([sha(b"a"), sha(b"b")]),
        "bytes_freed": 2,
        "skipped": [],
    }
    assert isinstance(report["version"], int)
    assert json.loads(json.dumps(report)) == report


def test_a_dry_run_changes_nothing_and_reports_the_same(clock: Clock, tmp_path: Path) -> None:
    roots = [tmp_path / "one", tmp_path / "two"]
    regs = []
    for root in roots:
        reg = LocalRegistry(root)
        a = commit_at(clock, reg, "orders", b"a", day(0))
        commit_at(clock, reg, "orders", b"b", day(1))
        commit_at(clock, reg, "orders", b"c", day(2))
        commit_at(clock, reg, "people", b"a", day(0))
        commit_at(clock, reg, "people", b"p", day(3))
        reg.tag("orders", "v1", a)
        (root / "objects" / "README").write_bytes(b"x")
        regs.append(reg)
    before = snapshot(roots[0])
    dry = regs[0].prune(day(10), dry_run=True)
    assert snapshot(roots[0]) == before
    real = regs[1].prune(day(10))
    assert dry["dry_run"] is True and real["dry_run"] is False
    assert {**dry, "dry_run": False} == real
    assert real["names"]["orders"]["entries_removed"] == 1
    assert real["objects_removed"] == [sha(b"b")]


def test_a_dry_run_does_not_need_the_lock(clock: Clock, tmp_path: Path) -> None:
    root = tmp_path / "reg"
    reg = four_days(clock, root)
    (root / "prune.lock").write_text("{}")
    assert reg.prune(day(10), dry_run=True)["names"]["orders"]["entries_removed"] == 3


def _contains(expected: Any, actual: Any, where: str = "report") -> None:
    if isinstance(expected, dict):
        assert isinstance(actual, dict), where
        for key, value in expected.items():
            assert key in actual, f"{where}.{key} is gone"
            _contains(value, actual[key], f"{where}.{key}")
    else:
        assert actual == expected, where


def test_report_v1_compatibility(clock: Clock, tmp_path: Path) -> None:
    """The v1 report of a fixed registry: every key and value it had is still there (new keys
    may be added within version 1; a removed or changed one needs version 2)."""
    golden = json.loads((DATA / "prune_report_v1.json").read_text("utf-8"))
    assert golden["format"] == "shape-registry-prune" and golden["version"] == 1
    reg = LocalRegistry(tmp_path / "reg")
    a = commit_at(clock, reg, "orders", b"one", day(0))
    commit_at(clock, reg, "orders", b"two", day(1))
    commit_at(clock, reg, "orders", b"three", day(2))
    commit_at(clock, reg, "orders", b"four", day(3))
    commit_at(clock, reg, "people", b"one", day(0))
    commit_at(clock, reg, "people", b"five", day(4))
    reg.tag("orders", "v1", a)
    (tmp_path / "reg" / "objects" / "NOTES.md").write_text("kept")
    report = reg.prune("2026-06-04T00:00:00Z", keep_last=1)
    _contains(golden, report)
    assert type(report["version"]) is int and report["version"] == golden["version"]


# ---- Wanted 4: crash safety and the lock --------------------------------------------------------


def _scenario(clock: Clock, root: Path) -> LocalRegistry:
    reg = LocalRegistry(root)
    for name in ("alpha", "beta", "gamma"):
        for n, data in enumerate((b"1", b"2", b"3")):
            commit_at(clock, reg, name, name.encode() + data, day(n))
    commit_at(clock, reg, "beta", b"shared", day(0))
    commit_at(clock, reg, "gamma", b"shared", day(0))
    commit_at(clock, reg, "beta", b"beta4", day(5))
    reg.tag("gamma", "keep", sha(b"gamma1"))
    return reg


class Boom(RuntimeError):
    pass


def test_an_interrupted_prune_leaves_a_working_registry(
    clock: Clock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "reg"
    reg = _scenario(clock, root)
    objects_before = object_ids(root)
    real_publish = local_mod._publish
    calls: list[Path] = []

    def publish(tmp: str, target: Path) -> None:
        if calls:
            raise Boom("interrupted")
        calls.append(target)
        real_publish(tmp, target)

    monkeypatch.setattr(local_mod, "_publish", publish)
    with pytest.raises(Boom):
        reg.prune(day(10))
    monkeypatch.setattr(local_mod, "_publish", real_publish)
    assert len(calls) == 1  # the first log was replaced
    assert len(reg.log(calls[0].name[: -len(".jsonl")])) < 4
    # no object was removed, no temporary file is left, the lock is released
    assert object_ids(root) == objects_before
    assert not [p for p in (root / "logs").iterdir() if p.name.startswith(".tmp-")]
    assert not (root / "prune.lock").exists()
    assert_nothing_dangles(reg)
    # a second prune completes the job: the result is the uninterrupted prune's
    reg.prune(day(10))
    other = tmp_path / "other"
    _scenario(clock, other).prune(day(10))
    assert snapshot(root) == snapshot(other)
    assert_nothing_dangles(reg)


def test_logs_are_replaced_atomically_in_their_directory(
    clock: Clock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "reg"
    reg = _scenario(clock, root)
    seen: list[tuple[Path, Path]] = []
    real_publish = local_mod._publish

    def publish(tmp: str, target: Path) -> None:
        seen.append((Path(tmp), target))
        assert object_ids(root) >= {sha(b"alpha1"), sha(b"beta1")}  # objects go last
        real_publish(tmp, target)

    monkeypatch.setattr(local_mod, "_publish", publish)
    reg.prune(day(10))
    assert seen
    for tmp, target in seen:
        assert tmp.parent == target.parent == root / "logs"
        assert tmp.name.startswith(".tmp-")
    assert sha(b"alpha1") not in object_ids(root)


def test_a_held_prune_lock_is_a_clear_error(clock: Clock, tmp_path: Path) -> None:
    root = tmp_path / "reg"
    reg = four_days(clock, root)
    (root / "prune.lock").write_text('{"pid": 1}')
    before = snapshot(root)
    with pytest.raises(RegistryError, match="prune.lock"):
        reg.prune(day(10))
    for action in (
        lambda: reg.commit("orders", b"new"),
        lambda: reg.tag("orders", "t"),
        lambda: reg.promote("orders", "latest", "production"),
    ):
        with pytest.raises(RegistryError, match="prune"):
            action()
    assert snapshot(root) == before
    # reading still works
    assert reg.checkout("orders") == b"d" and len(reg.log("orders")) == 4


def test_a_commit_cannot_race_a_running_prune(
    clock: Clock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "reg"
    reg = four_days(clock, root)
    other = LocalRegistry(root)  # a second process's view of the same registry
    refused: list[str] = []
    real_publish = local_mod._publish

    def publish(tmp: str, target: Path) -> None:
        with pytest.raises(RegistryError, match="prune") as exc:
            other.commit("orders", b"racing")
        refused.append(str(exc.value))
        real_publish(tmp, target)

    monkeypatch.setattr(local_mod, "_publish", publish)
    reg.prune(day(10))
    assert refused
    assert sha(b"racing") not in object_ids(root)
    assert not (root / "prune.lock").exists()
    # once the prune is done, commits work again
    monkeypatch.setattr(local_mod, "_publish", real_publish)
    assert other.commit("orders", b"after") == sha(b"after")


def test_a_prune_waits_for_a_commit_in_flight_then_gives_up(
    clock: Clock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "reg"
    reg = four_days(clock, root)
    marker = root / ".commit-0123abcd.lock"
    marker.write_text("{}")
    monkeypatch.setattr(local_mod, "_WRITER_WAIT", 0.05)
    before = snapshot(root)
    with pytest.raises(RegistryError, match=r"\.commit-0123abcd\.lock"):
        reg.prune(day(10))
    assert snapshot(root) == before
    assert not (root / "prune.lock").exists()
    marker.unlink()
    assert reg.prune(day(10))["names"]["orders"]["entries_removed"] == 3


def test_commits_leave_no_lock_behind(clock: Clock, tmp_path: Path) -> None:
    root = tmp_path / "reg"
    reg = four_days(clock, root)
    with pytest.raises(RegistryError):
        reg.commit("../x", b"x")
    reg.commit("orders", b"e")
    reg.tag("orders", "t")
    reg.promote("orders", "t", "production")
    reg.prune(day(10))
    assert not [p.name for p in root.iterdir() if p.name.endswith(".lock")]


def test_prune_then_commit_keeps_the_history_consistent(clock: Clock, tmp_path: Path) -> None:
    reg = four_days(clock, tmp_path / "reg")
    reg.prune(day(10))
    e = commit_at(clock, reg, "orders", b"e", day(11))
    assert [x["content_id"] for x in reg.log("orders")] == [sha(b"d"), e]
    assert reg.checkout("orders") == b"e"
    copy = tmp_path / "copy"
    shutil.copytree(tmp_path / "reg", copy)
    assert_nothing_dangles(LocalRegistry(copy))
