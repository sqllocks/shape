"""W3-09 (#72): the chaos ``duplicates`` mutator records true clusters, and its existing output for
existing seeds is unchanged."""

from __future__ import annotations

import hashlib
import json

import pyarrow as pa
import pytest

from shape.chaos.groundtruth import (
    Corruption,
    corrupt_tables,
    duplicate_clusters,
    parse_corruptions,
    read_ground_truth,
    write_ground_truth,
)

N = 300


def table() -> pa.Table:
    return pa.table(
        {
            "id": pa.array(range(N)),
            "name": [f"n{i}" for i in range(N)],
            "amt": [float(i) for i in range(N)],
        }
    )


def digest(outcome) -> str:  # type: ignore[no-untyped-def]
    return hashlib.sha256(
        json.dumps(outcome.records, sort_keys=True).encode()
        + json.dumps(outcome.tables["t"].to_pylist()).encode()
    ).hexdigest()


# Recorded from the mutator before W3-09 touched it (seeds 1, 7, 42).
BASELINE = {
    1: "8fbb11525a227f66db8a71f5da4dee92471785c8c8beb632f686c9e301173142",
    7: "22f63b2c44dbece2f55da36cbd0509f9c0ce6820532f9fc8f3005a7f52431665",
    42: "ff9b3d4b1041ac4cf8b3ef9bd38c12762ee05072a41c995feb6cd22df7596690",
}


@pytest.mark.parametrize("seed", sorted(BASELINE))
def test_existing_output_is_unchanged_for_existing_seeds(seed: int) -> None:
    out = corrupt_tables(
        {"t": table()},
        parse_corruptions(["duplicates=0.1", "duplicates=0.05@t"]),
        seed=seed,
        keys={"t": "id"},
    )
    assert digest(out) == BASELINE[seed]


def test_clusters_group_every_copy_with_its_source() -> None:
    out = corrupt_tables({"t": table()}, [Corruption("duplicates", 0.1)], seed=3, keys={"t": "id"})
    clusters = duplicate_clusters(out.records)["t"]
    assert len(clusters) == 30
    flat = [r for c in clusters for r in c]
    assert len(flat) == len(set(flat)) == 60
    for c in clusters:
        assert c[0] < N <= c[1]  # the original, then its copy
        rows = [out.tables["t"].slice(r, 1).to_pylist()[0] for r in c]
        assert rows[0] == rows[1]


def test_a_copy_of_a_copy_joins_the_same_cluster() -> None:
    out = corrupt_tables(
        {"t": table()},
        [Corruption("duplicates", 1.0), Corruption("duplicates", 0.5)],
        seed=2,
        keys={"t": "id"},
    )
    clusters = duplicate_clusters(out.records)["t"]
    assert len(clusters) == N  # every original is in one cluster; no cluster is split
    assert sum(len(c) for c in clusters) == out.tables["t"].num_rows
    assert all(min(c) < N for c in clusters)


def test_clusters_from_a_written_log_match_those_from_the_run(tmp_path) -> None:
    out = corrupt_tables({"t": table()}, [Corruption("duplicates", 0.2)], seed=9)
    path = write_ground_truth(tmp_path / "gt.jsonl", out)
    _, records = read_ground_truth(path)
    assert duplicate_clusters(records) == duplicate_clusters(out.records)


def test_other_corruptions_have_no_clusters() -> None:
    out = corrupt_tables({"t": table()}, [Corruption("negative_amounts", 0.1, "t", "amt")], seed=1)
    assert duplicate_clusters(out.records) == {}


def test_fuzz_option_makes_near_duplicates_and_leaves_exact_default_alone() -> None:
    plain = corrupt_tables(
        {"t": table()}, [Corruption("duplicates", 0.2)], seed=4, keys={"t": "id"}
    )
    fuzzy = corrupt_tables(
        {"t": table()},
        [Corruption("duplicates", 0.2, options={"fuzz": 1.0})],
        seed=4,
        keys={"t": "id"},
    )
    # the same rows are chosen as sources; only the copies differ, and the log says so
    src = lambda o: [r["source_row"] for r in o.records]  # noqa: E731
    assert src(plain) == src(fuzzy)
    assert fuzzy.tables["t"].slice(0, N).equals(table())
    assert fuzzy.tables["t"] != plain.tables["t"]
    copies = fuzzy.tables["t"].slice(N).to_pylist()
    originals = [table().slice(s, 1).to_pylist()[0] for s in src(fuzzy)]
    assert any(c["name"] != o["name"] for c, o in zip(copies, originals, strict=True))
    assert all(c["id"] == o["id"] for c, o in zip(copies, originals, strict=True))  # key untouched
    assert all(r["fuzz"] == 1.0 for r in fuzzy.records)
    assert all("fuzz" not in r for r in plain.records)
    again = corrupt_tables(
        {"t": table()},
        [Corruption("duplicates", 0.2, options={"fuzz": 1.0})],
        seed=4,
        keys={"t": "id"},
    )
    assert again.tables["t"].equals(fuzzy.tables["t"])


def test_fuzz_option_parses_and_validates() -> None:
    c = Corruption.parse("duplicates=0.1@t:fuzz=0.5")
    assert c.options == {"fuzz": 0.5}
    with pytest.raises(ValueError, match="fuzz"):
        Corruption("duplicates", 0.1, options={"fuzz": 2.0})
    with pytest.raises(ValueError, match="no option"):
        Corruption("duplicates", 0.1, options={"nope": 1})
