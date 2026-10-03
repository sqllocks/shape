"""W1-03 (#58): the dataset id, the reproducibility tuple, the manifest extension and replay."""

from __future__ import annotations

import datetime as dt
import decimal
import json
import platform
from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest

from shape.cli.main import main
from shape.kernel.dispatch import kernel_name
from shape.repro import (
    DATASET_ID_PREFIX,
    REPRODUCIBILITY_KEYS,
    dataset_id,
    reproducibility_tuple,
)
from shape.scenario import ManifestBuilder, PackLoader, PackRunner
from shape.scenario.manifest import MANIFEST_FORMAT, MANIFEST_VERSION, ManifestVersionError
from tests.scenario.conftest import PACK, write


def tables() -> dict[str, pa.Table]:
    return {
        "a": pa.table(
            {"id": [1, 2, 3, 3], "name": ["x", "y", None, None], "v": [0.5, 1.5, 2.5, 2.5]}
        ),
        "b": pa.table({"k": ["p", "q"], "flag": [True, False]}),
    }


# ---- the dataset id --------------------------------------------------------------------------


def test_the_id_is_a_prefixed_sha256_and_is_stable():
    a = dataset_id(tables())
    assert a.startswith(DATASET_ID_PREFIX) and len(a) == len(DATASET_ID_PREFIX) + 64
    assert a == dataset_id(tables())
    assert a == "sha256:" + a.split(":", 1)[1]


def test_the_id_ignores_row_order_column_order_and_table_order():
    t = tables()
    base = dataset_id(t)
    rows = t["a"].take(pa.array([3, 1, 0, 2]))
    cols = t["a"].select(["v", "id", "name"])
    reordered = {"b": t["b"].take(pa.array([1, 0])), "a": rows.select(["name", "v", "id"])}
    assert dataset_id({"a": rows, "b": t["b"]}) == base
    assert dataset_id({"a": cols, "b": t["b"]}) == base
    assert dataset_id(reordered) == base


def test_the_id_ignores_how_the_table_is_chunked():
    t = tables()
    chunked = {"a": pa.concat_tables([t["a"].slice(0, 1), t["a"].slice(1)]), "b": t["b"]}
    assert dataset_id(chunked) == dataset_id(t)


@pytest.mark.parametrize(
    "change",
    [
        lambda t: t["a"].set_column(0, "id", pa.array([1, 2, 3, 4])),  # one value
        lambda t: t["a"].slice(0, 3),  # a row fewer
        lambda t: pa.concat_tables([t["a"], t["a"].slice(0, 1)]),  # a row more
        lambda t: t["a"].set_column(0, "id", pa.array([1, 2, 3, 3], pa.int32())),  # a type
        lambda t: t["a"].set_column(0, "id", pa.array([1.0, 2.0, 3.0, 3.0])),  # int vs float
        lambda t: t["a"].rename_columns(["id", "label", "v"]),  # a column name
        lambda t: t["a"].set_column(1, "name", pa.array(["x", "y", "", None])),  # null vs empty
        lambda t: t["a"].set_column(
            2, "v", pa.array([0.5, 1.5, 2.5, float("nan")])
        ),  # value vs NaN
    ],
)
def test_the_id_changes_when_the_content_changes(change):
    t = tables()
    changed = dict(t)
    changed["a"] = change(t)
    assert dataset_id(changed) != dataset_id(t)


def test_a_duplicate_row_counts_and_null_differs_from_nan():
    one = {"t": pa.table({"x": [1.0, 2.0]})}
    two = {"t": pa.table({"x": [1.0, 2.0, 2.0]})}
    assert dataset_id(one) != dataset_id(two)
    assert dataset_id({"t": pa.table({"x": [None, 1.0]})}) != dataset_id(
        {"t": pa.table({"x": [float("nan"), 1.0]})}
    )


def test_swapping_values_between_columns_changes_the_id():
    a = {"t": pa.table({"x": [1, 2], "y": [2, 1]})}
    b = {"t": pa.table({"x": [2, 1], "y": [2, 1]})}
    assert dataset_id(a) != dataset_id(b)
    c = {"t": pa.table({"x": [1, 2], "y": [1, 2]})}  # same multiset of values, different rows
    assert dataset_id(a) != dataset_id(c)


def test_table_names_and_empty_tables_count():
    t = tables()
    assert dataset_id({"z": t["a"], "b": t["b"]}) != dataset_id(t)
    assert dataset_id({"e": pa.table({"x": pa.array([], pa.int64())})}) != dataset_id({})
    assert dataset_id({}) == dataset_id({})


def test_the_id_handles_every_common_type():
    t = {
        "t": pa.table(
            {
                "s": pa.array(["a", None]),
                "ls": pa.array(["a", None], pa.large_string()),
                "d": pa.array([dt.date(2020, 1, 2), None]),
                "ts": pa.array([dt.datetime(2020, 1, 2, 3), None]),
                "dec": pa.array([decimal.Decimal("1.50"), None], pa.decimal128(5, 2)),
                "bin": pa.array([b"ab", None]),
                "lst": pa.array([[1, 2], None]),
                "st": pa.array([{"a": 1}, None]),
                "dict": pa.array(["u", "v"]).dictionary_encode(),
                "i8": pa.array([1, None], pa.int8()),
            }
        )
    }
    first = dataset_id(t)
    assert first == dataset_id(t)
    flipped = {"t": t["t"].set_column(6, "lst", pa.array([[1, 3], None]))}
    assert dataset_id(flipped) != first


def test_string_and_large_string_are_one_type_for_the_id():
    a = {"t": pa.table({"s": pa.array(["a", "b"])})}
    b = {"t": pa.table({"s": pa.array(["a", "b"], pa.large_string())})}
    assert dataset_id(a) == dataset_id(b)


def test_the_id_is_independent_of_the_kernel(monkeypatch):
    from shape.kernel import dispatch

    t = tables()
    ids = {}
    for mode in ("python", "auto"):
        monkeypatch.setenv("SHAPE_KERNEL", mode)
        dispatch.reset()
        ids[mode] = dataset_id(t)
    dispatch.reset()
    assert ids["python"] == ids["auto"]


def test_a_large_table_ids_quickly_and_ignores_row_order():
    rng = np.random.default_rng(0)
    n = 200_000
    t = pa.table(
        {"i": np.arange(n), "f": rng.normal(size=n), "s": [f"v{i % 977}" for i in range(n)]}
    )
    perm = rng.permutation(n)
    assert dataset_id({"t": t}) == dataset_id({"t": t.take(pa.array(perm))})


# ---- the reproducibility tuple ---------------------------------------------------------------


def test_the_tuple_has_the_seven_fields():
    from shape import __version__
    from shape.generation.schema import SCHEMA_VERSION
    from shape.profile.engine import SCHEMA_VERSION as PROFILE_VERSION

    got = reproducibility_tuple(seed=7, scale="small")
    assert set(got) == set(REPRODUCIBILITY_KEYS) == {
        "schema_version", "profile_version", "seed", "scale", "shape_version", "kernel", "platform",
    }  # fmt: skip
    assert got["schema_version"] == SCHEMA_VERSION and got["profile_version"] == PROFILE_VERSION
    assert got["seed"] == 7 and got["scale"] == "small" and got["shape_version"] == __version__
    assert got["kernel"] == kernel_name()
    assert got["platform"] == f"{platform.system().lower()}-{platform.machine().lower()}"
    json.dumps(got)


# ---- the manifest ----------------------------------------------------------------------------


def run(tmp_path, retail, *, seed=42, out="out", text=None):
    pack = PackLoader().load(write(tmp_path / "p.yaml", text or PACK.format(fmt="parquet")))
    return PackRunner().run(pack, retail, "fabric_demo", seed, tmp_path / out)


def manifest_dict(result) -> dict:
    return json.loads(Path(result.files_written[-1]).read_text())


def test_a_run_records_the_tuple_and_the_dataset_id(tmp_path, retail):
    result = run(tmp_path, retail)
    m = manifest_dict(result)
    assert (
        m["format"] == MANIFEST_FORMAT == "shape-run-manifest"
        and m["version"] == MANIFEST_VERSION == 1
    )
    assert set(m["reproducibility"]) == set(REPRODUCIBILITY_KEYS)
    assert m["reproducibility"]["seed"] == 42 and m["reproducibility"]["scale"] == "fabric_demo"
    assert m["dataset_id"].startswith(DATASET_ID_PREFIX)
    assert result.manifest.dataset_id == m["dataset_id"]  # type: ignore[union-attr]
    assert ManifestBuilder.from_file(result.files_written[-1]).to_dict() == m


def test_the_dataset_id_follows_the_seed_not_the_clock(tmp_path, retail):
    a = run(tmp_path, retail, out="a")
    b = run(tmp_path, retail, out="b")
    c = run(tmp_path, retail, out="c", seed=43)
    assert manifest_dict(a)["dataset_id"] == manifest_dict(b)["dataset_id"]
    assert manifest_dict(a)["dataset_id"] != manifest_dict(c)["dataset_id"]


def test_a_manifest_from_before_this_change_still_loads(tmp_path, retail):
    m = manifest_dict(run(tmp_path, retail))
    for key in ("format", "version", "reproducibility", "dataset_id"):
        del m[key]
    old = tmp_path / "old_manifest.json"
    old.write_text(json.dumps(m))
    loaded = ManifestBuilder.from_file(old)
    assert loaded.dataset_id == "" and loaded.reproducibility == {}


def test_a_manifest_from_a_newer_shape_is_refused_with_a_clear_message(tmp_path, retail):
    m = manifest_dict(run(tmp_path, retail))
    m["version"] = MANIFEST_VERSION + 1
    newer = tmp_path / "newer_manifest.json"
    newer.write_text(json.dumps(m))
    with pytest.raises(ManifestVersionError, match="newer"):
        ManifestBuilder.from_file(newer)


def test_a_manifest_that_is_not_a_run_manifest_is_refused(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"format": "something-else", "version": 1}))
    with pytest.raises(ValueError, match="not a run manifest"):
        ManifestBuilder.from_file(p)


# ---- replay ----------------------------------------------------------------------------------


@pytest.fixture
def ran(tmp_path, retail):
    pack_file = write(tmp_path / "p.yaml", PACK.format(fmt="parquet"))
    pack = PackLoader().load(pack_file)
    result = PackRunner().run(pack, retail, "fabric_demo", 42, tmp_path / "out")
    return pack_file, Path(result.files_written[-1]), tmp_path


def test_replay_regenerates_the_dataset_and_checks_the_id(ran, capsys):
    pack_file, manifest, tmp = ran
    before = sorted(p.name for p in tmp.rglob("*"))
    assert main(["pack", "replay", str(manifest), str(pack_file)]) == 0
    out = capsys.readouterr().out
    assert "MATCH" in out and json.loads(manifest.read_text())["dataset_id"] in out
    assert sorted(p.name for p in tmp.rglob("*")) == before  # replay writes nothing next to the run


def test_replay_json(ran, capsys):
    pack_file, manifest, _ = ran
    assert main(["pack", "replay", str(manifest), str(pack_file), "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["match"] is True and doc["expected"] == doc["actual"] and doc["differences"] == []


def test_replay_of_a_changed_dataset_id_is_exit_1_and_names_both_ids(ran, capsys):
    pack_file, manifest, tmp = ran
    doc = json.loads(manifest.read_text())
    real = doc["dataset_id"]
    doc["dataset_id"] = DATASET_ID_PREFIX + "0" * 64
    bad = tmp / "bad_manifest.json"
    bad.write_text(json.dumps(doc))
    assert main(["pack", "replay", str(bad), str(pack_file)]) == 1
    out = capsys.readouterr().out
    assert "MISMATCH" in out and "0" * 64 in out and real in out


def test_replay_uses_the_seed_in_the_manifest(ran, capsys):
    pack_file, manifest, tmp = ran
    doc = json.loads(manifest.read_text())
    doc["seed"] = 43
    doc["reproducibility"]["seed"] = 43
    other = tmp / "seed43_manifest.json"
    other.write_text(json.dumps(doc))
    assert main(["pack", "replay", str(other), str(pack_file)]) == 1


def test_replay_reports_environment_differences_without_failing_a_match(ran, capsys):
    pack_file, manifest, tmp = ran
    doc = json.loads(manifest.read_text())
    doc["reproducibility"]["platform"] = "plan9-mips"
    doc["reproducibility"]["kernel"] = "python" if kernel_name() == "rust" else "rust"
    moved = tmp / "moved_manifest.json"
    moved.write_text(json.dumps(doc))
    assert main(["pack", "replay", str(moved), str(pack_file), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["match"] is True
    assert {d["field"] for d in result["differences"]} == {"platform", "kernel"}


def test_replay_names_the_tuple_fields_that_differ_on_a_mismatch(ran, capsys):
    pack_file, manifest, tmp = ran
    doc = json.loads(manifest.read_text())
    doc["dataset_id"] = DATASET_ID_PREFIX + "1" * 64
    doc["reproducibility"]["shape_version"] = "0.0.1"
    bad = tmp / "v_manifest.json"
    bad.write_text(json.dumps(doc))
    assert main(["pack", "replay", str(bad), str(pack_file)]) == 1
    assert "shape_version" in capsys.readouterr().out


def test_replay_refuses_what_it_cannot_check_with_exit_2(ran, capsys):
    pack_file, manifest, tmp = ran
    doc = json.loads(manifest.read_text())
    legacy = dict(doc)
    for key in ("format", "version", "reproducibility", "dataset_id"):
        del legacy[key]
    old = tmp / "legacy_manifest.json"
    old.write_text(json.dumps(legacy))
    assert main(["pack", "replay", str(old), str(pack_file)]) == 2
    assert "dataset id" in capsys.readouterr().err
    wrong = dict(doc, pack_id="other")
    (tmp / "w_manifest.json").write_text(json.dumps(wrong))
    assert main(["pack", "replay", str(tmp / "w_manifest.json"), str(pack_file)]) == 2
    assert "other" in capsys.readouterr().err
    hashed = dict(doc, spec_hash="ab" * 32)
    (tmp / "h_manifest.json").write_text(json.dumps(hashed))
    assert main(["pack", "replay", str(tmp / "h_manifest.json"), str(pack_file)]) == 2
    assert "spec" in capsys.readouterr().err
    assert main(["pack", "replay", str(tmp / "missing.json"), str(pack_file)]) == 2


def run_spec_with_cli(fixtures, tmp_path, name, capsys):
    import shutil

    for f in ("tutorial_custom_pack.yaml", name):
        shutil.copy(fixtures / f, tmp_path / f)
    out = tmp_path / "specout"
    assert main(["pack", "run", str(tmp_path / name), "-o", str(out)]) == 0
    capsys.readouterr()
    return tmp_path / name, next(out.glob("*_manifest.json"))


def test_replay_of_a_run_with_a_spec_checks_the_spec_hash(fixtures, tmp_path, capsys):
    spec_file, manifest = run_spec_with_cli(fixtures, tmp_path, "retail_basic.gsl.yaml", capsys)
    assert json.loads(manifest.read_text())["spec_hash"]
    assert main(["pack", "replay", str(manifest), str(spec_file)]) == 0
    capsys.readouterr()
    spec_file.write_text(spec_file.read_text() + "\n# edited\n")
    assert main(["pack", "replay", str(manifest), str(spec_file)]) == 2
    assert "spec" in capsys.readouterr().err


def test_a_run_with_chaos_replays_to_the_same_id(fixtures, tmp_path, capsys):
    spec_file, manifest = run_spec_with_cli(fixtures, tmp_path, "retail_chaos.gsl.yaml", capsys)
    assert json.loads(manifest.read_text())["chaos"]
    assert main(["pack", "replay", str(manifest), str(spec_file)]) == 0
