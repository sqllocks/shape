"""HUNT2-profile: regression tests for mergeable profiles (#592, #593, #594, #595, #599).

Every test runs on both kernels (the ``kernel`` fixture), since the sketch state is the kernel's.
"""

from __future__ import annotations

import datetime as dt
import decimal
import warnings
from collections.abc import Iterator
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import shape
from shape.artifact.io import ArtifactError
from shape.kernel import dispatch
from shape.profile import MergeError, merge_profiles
from shape.profile.reference.profile import Profile

D = decimal.Decimal


@pytest.fixture(params=["rust", "python"])
def kernel(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv("SHAPE_KERNEL", request.param)
    dispatch.reset()
    yield request.param
    dispatch.reset()


def _profile(tmp_path: Path, name: str, table: pa.Table, sketches: bool = True) -> Profile:
    path = tmp_path / f"{name}.parquet"
    pq.write_table(table, path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return shape.profile(str(path), sketches=sketches)


def _col(p: Profile, column: str) -> dict:
    return next(iter(p.tables.values()))["columns"][column]


# --------------------------------------------------------------------- #592 other extremes

_KINDS = {
    "decimal": (
        pa.array([D("2.20"), D("-1.10"), None], pa.decimal128(10, 2)),
        pa.array([D("3.30"), D("0.05")], pa.decimal128(10, 2)),
    ),
    "time": (
        pa.array([dt.time(1, 2), dt.time(23, 59, 59, 5)]),
        pa.array([dt.time(0, 0, 1), None]),
    ),
    "binary": (pa.array([b"b", b"zz", None]), pa.array([b"a", "\u00e9".encode()])),
    "duration": (
        pa.array([1, 90061, None], pa.duration("s")),
        pa.array([-1, 5], pa.duration("s")),
    ),
}


@pytest.mark.parametrize("exact_only", [True, False])
@pytest.mark.parametrize("kind", sorted(_KINDS))
def test_592_other_extremes_merge_like_the_union(
    tmp_path: Path, kernel: str, kind: str, exact_only: bool
) -> None:
    a_arr, b_arr = _KINDS[kind]
    a = _profile(tmp_path, "a", pa.table({"c": a_arr}))
    b = _profile(tmp_path, "b", pa.table({"c": b_arr}))
    whole = _profile(tmp_path, "w", pa.table({"c": pa.concat_arrays([a_arr, b_arr])}))
    merged = _col(merge_profiles([a, b], exact_only=exact_only), "c")
    expected = _col(whole, "c")
    assert merged["min_value"] == expected["min_value"]
    assert merged["max_value"] == expected["max_value"]
    assert merged["null_count"] == expected["null_count"]


def test_592_extremes_of_different_kinds_are_still_refused(tmp_path: Path, kernel: str) -> None:
    a = _profile(tmp_path, "a", pa.table({"c": pa.array([b"a"])}), sketches=False)
    b = _profile(tmp_path, "b", pa.table({"c": pa.array(["text"])}), sketches=False)
    with pytest.raises(MergeError, match="column 'c'"):
        merge_profiles([a, b], exact_only=True)


# ------------------------------------------------------------------ #593 column order


def test_593_sketched_merge_matches_columns_by_name(tmp_path: Path, kernel: str) -> None:
    a = _profile(tmp_path, "a", pa.table({"id": [1, 2], "name": ["x", "y"]}))
    b = _profile(tmp_path, "b", pa.table({"name": ["z", "x"], "id": [3, 1]}))
    whole = _profile(tmp_path, "w", pa.table({"id": [1, 2, 3, 1], "name": ["x", "y", "z", "x"]}))
    merged = merge_profiles([a, b])
    for column in ("id", "name"):
        assert _col(merged, column)["cardinality"] == _col(whole, column)["cardinality"] == 3
    assert list(next(iter(merged.tables.values()))["columns"]) == ["id", "name"]
    # the merged state is in the first input's column order and merges again
    again = merge_profiles([merged, b])
    assert _col(again, "id")["cardinality"] == 3
    assert list(next(iter(again.tables.values()))["columns"]) == ["id", "name"]


def test_593_a_real_type_difference_is_still_refused_by_name(tmp_path: Path, kernel: str) -> None:
    a = _profile(tmp_path, "a", pa.table({"id": [1, 2], "name": ["x", "y"]}))
    b = _profile(tmp_path, "b", pa.table({"name": ["z"], "id": ["3"]}))
    with pytest.raises(MergeError, match=r"id: int64 vs string") as info:
        merge_profiles([a, b])
    assert "name" not in str(info.value).split("(", 1)[1].split(")", 1)[0]


# ------------------------------------------------------------- #594 empty or all-null parts


def _csv_profiles(tmp_path: Path) -> list[Profile]:
    (tmp_path / "p1.csv").write_text("id,amt\n1,2.5\n2,3.5\n")
    (tmp_path / "p2.csv").write_text("id,amt\n3,\n4,\n")
    (tmp_path / "p3.csv").write_text("id,amt\n")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return [
            shape.profile(str(tmp_path / f"{n}.csv"), sketches=True) for n in ("p1", "p2", "p3")
        ]


def test_594_an_all_null_partition_merges_with_sketches(tmp_path: Path, kernel: str) -> None:
    p1, p2, _ = _csv_profiles(tmp_path)
    merged = merge_profiles([p1, p2])
    amt, ids = _col(merged, "amt"), _col(merged, "id")
    assert amt["null_count"] == 2 and amt["cardinality"] == 2
    assert ids["cardinality"] == 4 and ids["null_count"] == 0
    assert merged.tables[merged.name]["row_count"] == 4
    # the all-null part first: its null-typed column takes the type of the part with values
    swapped = merge_profiles([p2, p1])
    assert _col(swapped, "amt")["cardinality"] == 2 and _col(swapped, "amt")["null_count"] == 2
    # and the merged state merges again
    assert _col(merge_profiles([merged, p2]), "amt")["null_count"] == 4


def test_594_an_empty_partition_merges_with_sketches(tmp_path: Path, kernel: str) -> None:
    p1, _, p3 = _csv_profiles(tmp_path)
    for parts in ([p1, p3], [p3, p1]):
        merged = merge_profiles(parts)
        assert merged.tables[merged.name]["row_count"] == 2
        assert _col(merged, "id")["cardinality"] == 2
        assert _col(merged, "amt")["cardinality"] == 2
    only_empty = merge_profiles([p3, p3])
    assert only_empty.tables[only_empty.name]["row_count"] == 0


# ------------------------------------------------------------------- #595 newer versions


def _two(tmp_path: Path) -> tuple[Profile, Profile]:
    a = _profile(tmp_path, "a", pa.table({"x": [1, 2, 3]}))
    b = _profile(tmp_path, "b", pa.table({"x": [4, 5]}))
    return a, b


def test_595_a_newer_snapshot_version_is_refused(tmp_path: Path, kernel: str) -> None:
    a, b = _two(tmp_path)
    sk = a.sketches
    sk["snapshot_version"] = 2
    newer = Profile(a.to_dict(), name=a.name, sketches=sk)
    with pytest.raises(MergeError, match="newer"):
        merge_profiles([newer, b])
    shape.save(newer, tmp_path / "a2.shape")
    with pytest.raises(ArtifactError, match=r"snapshot.*version 2.*upgrade Shape"):
        shape.load(tmp_path / "a2.shape")


@pytest.mark.parametrize("bad", ["1", 0, True, None])
def test_595_a_malformed_snapshot_version_is_refused(tmp_path: Path, kernel: str, bad) -> None:
    a, _ = _two(tmp_path)
    sk = a.sketches
    sk["snapshot_version"] = bad
    shape.save(Profile(a.to_dict(), name=a.name, sketches=sk), tmp_path / "bad.shape")
    with pytest.raises(ArtifactError, match="snapshot"):
        shape.load(tmp_path / "bad.shape")


def test_595_the_current_snapshot_version_still_loads(tmp_path: Path, kernel: str) -> None:
    a, b = _two(tmp_path)
    shape.save(a, tmp_path / "a.shape")
    loaded = shape.load(tmp_path / "a.shape")
    assert merge_profiles([loaded, b]).tables["a"]["row_count"] == 5


def test_595_a_newer_merge_block_is_refused(tmp_path: Path, kernel: str) -> None:
    a, b = _two(tmp_path)
    m = merge_profiles([a, b])
    data = m.to_dict()
    data["merge"]["version"] = 2
    newer = Profile(data, name=m.name, sketches=m.sketches)
    with pytest.raises(MergeError, match="newer"):
        merge_profiles([newer, b])
    shape.save(newer, tmp_path / "m2.shape")
    with pytest.raises(ArtifactError, match=r"merge.*version 2.*upgrade Shape"):
        shape.load(tmp_path / "m2.shape")
    # the current version reads, and merges again
    shape.save(m, tmp_path / "m1.shape")
    assert merge_profiles([shape.load(tmp_path / "m1.shape"), b]).tables["a"]["row_count"] == 7


@pytest.mark.parametrize(
    "block",
    [{"format": "other", "version": 1}, {"format": "shape-profile-merge", "version": "1"}, []],
)
def test_595_a_malformed_merge_block_is_refused(tmp_path: Path, kernel: str, block) -> None:
    a, b = _two(tmp_path)
    m = merge_profiles([a, b])
    data = m.to_dict()
    data["merge"] = block
    shape.save(Profile(data, name=m.name, sketches=m.sketches), tmp_path / "bad.shape")
    with pytest.raises(ArtifactError, match="merge"):
        shape.load(tmp_path / "bad.shape")


# -------------------------------------------------------------------- #599 promoted tags


def test_599_promoted_extremes_are_tagged_like_the_union(tmp_path: Path, kernel: str) -> None:
    a_t = pa.table(
        {
            "x": pa.array([1, 9], pa.int64()),
            "t": pa.array([dt.date(2020, 1, 1), dt.date(2020, 1, 5)]),
        }
    )
    b_t = pa.table(
        {
            "x": pa.array([3.5, 7.25]),
            "t": pa.array([dt.datetime(2020, 1, 3, 12), dt.datetime(2020, 1, 4)]),
        }
    )
    a, b = _profile(tmp_path, "a", a_t, False), _profile(tmp_path, "b", b_t, False)
    whole = _profile(
        tmp_path,
        "w",
        pa.table(
            {
                "x": pa.array([1.0, 9.0, 3.5, 7.25]),
                "t": pa.array(
                    [
                        dt.datetime(2020, 1, 1),
                        dt.datetime(2020, 1, 5),
                        dt.datetime(2020, 1, 3, 12),
                        dt.datetime(2020, 1, 4),
                    ]
                ),
            }
        ),
        False,
    )
    for parts in ([a, b], [b, a]):
        merged = merge_profiles(parts, exact_only=True)
        for column in ("x", "t"):
            for field in ("min_value", "max_value"):
                assert _col(merged, column)[field] == _col(whole, column)[field], (column, field)


def test_599_unpromoted_extremes_keep_their_tag(tmp_path: Path, kernel: str) -> None:
    a = _profile(tmp_path, "a", pa.table({"x": pa.array([1, 2])}), False)
    b = _profile(tmp_path, "b", pa.table({"x": pa.array([5, 3])}), False)
    merged = merge_profiles([a, b], exact_only=True)
    assert _col(merged, "x")["min_value"] == ["int", 1]
    assert _col(merged, "x")["max_value"] == ["int", 5]
