"""W8-03: ``shape registry ROOT prune`` (issue #566, Wanted 5) and every reader of a registry
after a prune (Wanted 6): ``log``, ``list``, ``show``, ``tag``, ``checkout``, ``diff``,
``bisect`` and ``timelapse`` work on the remaining entries, and no ref or tag dangles."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from prune_helpers import Clock, assert_nothing_dangles, commit_at, day, object_ids, snapshot

from shape.cli.main import main
from shape.registry import LocalRegistry


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def result(out: str) -> dict[str, Any]:
    """The prune report inside the ``shape-result`` envelope ``--json`` prints (W1-14)."""
    env = json.loads(out)
    assert env["format"] == "shape-result" and env["version"] == 1
    assert env["command"] == "registry prune" and env["exit_code"] == 0
    report: dict[str, Any] = env["payload"]
    return report


def doc(n: int) -> bytes:
    return json.dumps({"version": n, "rows": 100 + n, "status": "ok"}, sort_keys=True).encode()


@pytest.fixture()
def registry(clock: Clock, tmp_path: Path) -> Path:
    """``orders``: six JSON versions on days 0 to 5, tag ``v0`` on day 0, ref ``production`` on
    day 2; ``people``: two versions on days 0 and 1."""
    root = tmp_path / "reg"
    reg = LocalRegistry(root)
    ids = [commit_at(clock, reg, "orders", doc(n), day(n)) for n in range(6)]
    reg.tag("orders", "v0", ids[0])
    reg.promote("orders", ids[2], "production")
    commit_at(clock, reg, "people", b"p0", day(0))
    commit_at(clock, reg, "people", b"p1", day(1))
    return root


# ---- Wanted 5: the command ----------------------------------------------------------------------


def test_json_prints_the_report_of_the_api(
    registry: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import shutil

    copy = tmp_path / "copy"
    shutil.copytree(registry, copy)
    code, out, err = run(
        capsys, "registry", str(registry), "prune", "--before", "2026-06-05", "--json"
    )
    assert code == 0, err
    report = result(out)
    assert report == LocalRegistry(copy).prune("2026-06-05T00:00:00Z")
    assert report["format"] == "shape-registry-prune" and report["version"] == 1
    assert report["names"]["orders"]["entries_removed"] == 2  # days 1 and 3
    assert report["names"]["people"]["entries_removed"] == 1
    assert_nothing_dangles(LocalRegistry(registry))


def test_the_summary_names_what_was_removed(
    registry: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = run(capsys, "registry", str(registry), "prune", "--before", "2026-06-05")
    assert code == 0
    assert "orders: 2 entries removed, 4 kept" in out
    assert "people: 1 entries removed, 1 kept" in out
    assert "objects removed: 3" in out
    freed = len(doc(1)) + len(doc(3)) + len(b"p0")
    assert f"bytes freed: {freed}" in out
    assert "dry run" not in out


def test_dry_run_changes_nothing(registry: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = snapshot(registry)
    code, out, _ = run(
        capsys, "registry", str(registry), "prune", "--before", "2026-06-05", "--dry-run"
    )
    assert code == 0 and "dry run" in out and "orders: 2 entries removed" in out
    assert snapshot(registry) == before
    code, out, _ = run(
        capsys,
        "registry",
        str(registry),
        "prune",
        "--before",
        "2026-06-05",
        "--dry-run",
        "--json",
    )
    assert code == 0 and result(out)["dry_run"] is True
    assert snapshot(registry) == before


def test_name_and_keep_last(registry: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(
        capsys,
        "registry",
        str(registry),
        "prune",
        "--before",
        "2026-07-01T00:00:00Z",
        "--name",
        "orders",
        "--keep-last",
        "2",
        "--json",
    )
    assert code == 0
    report = result(out)
    assert set(report["names"]) == {"orders"}
    # kept: v0 (tag), day 2 (production), days 4 and 5 (keep-last 2; day 5 is latest)
    reg = LocalRegistry(registry)
    assert [
        json.loads(reg.checkout("orders", e["content_id"]))["version"] for e in reg.log("orders")
    ] == [0, 2, 4, 5]
    assert len(reg.log("people")) == 2


def test_name_is_repeatable(registry: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(
        capsys,
        "registry",
        str(registry),
        "prune",
        "--before",
        "2026-07-01",
        "--name",
        "orders",
        "--name",
        "people",
        "--json",
    )
    assert code == 0 and set(result(out)["names"]) == {"orders", "people"}


@pytest.mark.parametrize(
    "args",
    [
        ["--before", "yesterday"],
        ["--before", "2026-06-05T00:00:00"],  # no zone
        ["--before", "2026-02-30"],
        ["--before", "2026-06-05", "--name", "nope"],
        ["--before", "2026-06-05", "--name", "../x"],
        ["--before", "2026-06-05", "--keep-last", "0"],
        ["--before", "2026-06-05", "--keep-last", "-3"],
        ["--before", "2026-06-05", "--keep-last", "two"],
        [],  # --before is required
    ],
)
def test_bad_input_exits_2_and_changes_nothing(
    registry: Path, capsys: pytest.CaptureFixture[str], args: list[str]
) -> None:
    before = snapshot(registry)
    try:
        code, _, err = run(capsys, "registry", str(registry), "prune", *args)
    except SystemExit as exc:  # argparse: a missing or malformed flag
        code, err = int(exc.code or 0), capsys.readouterr().err
    assert code == 2
    assert err.strip()
    assert snapshot(registry) == before


def test_json_bad_input_is_an_envelope_with_exit_2(
    registry: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = run(
        capsys,
        "registry",
        str(registry),
        "prune",
        "--before",
        "2026-06-05",
        "--name",
        "nope",
        "--json",
    )
    env = json.loads(out)
    assert code == 2 and env["exit_code"] == 2 and env["command"] == "registry prune"
    assert "nope" in env["error"]


def test_the_exit_codes_are_registered() -> None:
    from shape.cli.exitcodes import codes

    assert set(codes("registry prune")) == {0, 2}


def test_a_held_lock_exits_2(registry: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (registry / "prune.lock").write_text("{}")
    code, _, err = run(capsys, "registry", str(registry), "prune", "--before", "2026-06-05")
    assert code == 2 and "prune.lock" in err
    code, _, err = run(capsys, "registry", str(registry), "tag", "orders", "t")
    assert code == 2 and "prune" in err


def test_prune_is_in_the_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["registry", "x", "prune", "--help"])
    out = capsys.readouterr().out
    for flag in ("--before", "--name", "--keep-last", "--dry-run", "--json"):
        assert flag in out


# ---- Wanted 6: every reader after a prune -------------------------------------------------------


def _pruned(registry: Path, capsys: pytest.CaptureFixture[str]) -> LocalRegistry:
    code, _, err = run(capsys, "registry", str(registry), "prune", "--before", "2026-06-07")
    assert code == 0, err
    reg = LocalRegistry(registry)
    # kept: v0 (tag), day 2 (production), day 5 (latest, keep-last)
    versions = [
        json.loads(reg.checkout("orders", e["content_id"]))["version"] for e in reg.log("orders")
    ]
    assert versions == [0, 2, 5]
    return reg


def test_log_and_list_after_a_prune(registry: Path, capsys: pytest.CaptureFixture[str]) -> None:
    reg = _pruned(registry, capsys)
    code, out, _ = run(capsys, "registry", str(registry), "log", "orders")
    assert code == 0
    assert [e["content_id"] for e in json.loads(out)] == [
        e["content_id"] for e in reg.log("orders")
    ]
    code, out, _ = run(capsys, "registry", str(registry), "list")
    rows = {r["name"]: r for r in json.loads(out)}
    assert rows["orders"]["commits"] == 3 and rows["people"]["commits"] == 1
    assert rows["orders"]["latest"] == reg.resolve("orders") and rows["orders"]["tags"] == ["v0"]


def test_show_and_checkout_every_ref_and_tag(
    registry: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    reg = _pruned(registry, capsys)
    for name in reg.names():
        refs = [*reg.refs(name), *reg.tags(name)]
        assert refs
        for ref in refs:
            code, out, err = run(capsys, "registry", str(registry), "show", name, ref)
            assert code == 0, err
            assert json.loads(out)["content_id"] == reg.resolve(name, ref)
            out_file = tmp_path / f"{name}-{ref}.bin"
            code, _, err = run(
                capsys, "registry", str(registry), "checkout", name, ref, "-o", str(out_file)
            )
            assert code == 0, err
            assert out_file.read_bytes() == reg.checkout(name, ref)


def test_a_removed_version_is_no_longer_recorded(
    registry: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    removed = LocalRegistry(registry).log("orders")[1]["content_id"]
    _pruned(registry, capsys)
    assert removed not in object_ids(registry)
    code, _, err = run(capsys, "registry", str(registry), "show", "orders", removed)
    assert code == 2 and "not recorded" in err
    code, _, err = run(capsys, "registry", str(registry), "tag", "orders", "late", removed)
    assert code == 2 and "not recorded" in err


def test_tag_and_diff_after_a_prune(registry: Path, capsys: pytest.CaptureFixture[str]) -> None:
    reg = _pruned(registry, capsys)
    code, out, _ = run(capsys, "registry", str(registry), "tag", "orders", "v2", "production")
    assert code == 0 and json.loads(out)["content_id"] == reg.resolve("orders", "production")
    code, out, err = run(capsys, "registry", str(registry), "diff", "orders", "v0", "latest")
    assert code == 0, err
    assert json.loads(out)["changed"]
    code, _, err = run(capsys, "registry", str(registry), "diff", "orders", "v2", "production")
    assert code == 0, err
    assert_nothing_dangles(reg)


def _profile_history(clock: Clock, root: Path, tmp: Path) -> list[str]:
    """Six raw profiles of ``feed`` on days 0 to 5; column ``v`` moves by 5 from day 4 on."""
    import numpy as np
    import pandas as pd

    import shape

    reg = LocalRegistry(root)
    ids = []
    for n in range(6):
        grid = np.linspace(-2.0, 2.0, 300 + n)
        frame = pd.DataFrame({"v": grid + (5.0 if n >= 4 else 0.0), "w": grid * 2.0 + 10.0})
        prof = shape.profile(frame, name="feed", sketches=True)
        path = tmp / f"feed-{n}.shape"
        shape.save(prof, path, capture="full")  # a raw profile: bisect needs one (W1-11)
        clock.at(day(n))
        meta: dict[str, Any] = {"business_date": f"2026-06-{n + 1:02d}"}
        ids.append(reg.commit("feed", path.read_bytes(), meta, allow_raw=True))
    reg.tag("feed", "good", ids[1])
    return ids


def test_bisect_and_timelapse_after_a_prune(
    clock: Clock, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "hist"
    ids = _profile_history(clock, root, tmp_path)
    code, _, err = run(capsys, "registry", str(root), "prune", "--before", "2026-06-04")
    assert code == 0, err
    reg = LocalRegistry(root)
    # kept: day 1 (tag good), day 3 (on the cutoff), days 4 and 5
    assert [e["content_id"] for e in reg.log("feed")] == [ids[1], ids[3], ids[4], ids[5]]
    code, out, err = run(
        capsys,
        "bisect",
        str(root),
        "feed",
        "--good",
        "good",
        "--bad",
        "latest",
        "--column",
        "v",
        "--json",
        "--no-project",
    )
    assert code == 0, err
    assert json.loads(out)["first_bad"]["content_id"] == ids[4]
    code, out, err = run(capsys, "timelapse", str(root), "feed", "--column", "v", "--no-project")
    assert code == 0, err
    frames = json.loads(out)["frames"]
    assert [f["content_ids"] for f in frames] == [[ids[1]], [ids[3]], [ids[4]], [ids[5]]]
    assert_nothing_dangles(reg)
