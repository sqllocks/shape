"""W3-09 (#72): the synthetic duplicates generator records true clusters, and resolution is scored
against them (precision, recall and F1, with fixed thresholds)."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pyarrow as pa
import pytest

from shape.resolve import (
    BlockRule,
    FieldMatch,
    ResolveConfig,
    Survivorship,
    make_duplicates,
    pair_metrics,
    resolve,
)
from shape.resolve.synth import read_truth, write_truth

FIRST = (
    "james mary robert patricia john jennifer michael linda william elizabeth david "
    "barbara richard susan joseph jessica thomas sarah charles karen"
).split()
LAST = (
    "smith johnson williams brown jones garcia miller davis rodriguez martinez hernandez "
    "lopez gonzalez wilson anderson thomas taylor moore jackson martin"
).split()
CITY = ["boston", "denver", "austin", "seattle", "miami", "chicago", "dallas", "phoenix"]


def people(n: int = 600, seed: int = 5) -> pa.Table:
    rng = np.random.default_rng(seed)
    first = [FIRST[i] for i in rng.integers(0, len(FIRST), n)]
    last = [LAST[i] for i in rng.integers(0, len(LAST), n)]
    # a distinguishing surname suffix so distinct people are not near-identical strings
    last = [
        f"{ln}{chr(97 + int(k) % 26)}{chr(97 + int(k // 26) % 26)}"
        for ln, k in zip(last, rng.permutation(n), strict=True)
    ]
    base = dt.date(1950, 1, 1)
    return pa.table(
        {
            "id": pa.array(range(1, n + 1), pa.int64()),
            "name": [f"{f} {ln}" for f, ln in zip(first, last, strict=True)],
            "city": [CITY[i] for i in rng.integers(0, len(CITY), n)],
            "income": pa.array(np.round(rng.uniform(20_000, 150_000, n), 2)),
            "born": pa.array(
                [base + dt.timedelta(days=int(d)) for d in rng.integers(0, 20_000, n)]
            ),
        }
    )


CONFIG = ResolveConfig(
    blocks=[BlockRule("name", "ngram", 5), BlockRule("name", "phonetic")],
    fields=[
        FieldMatch("name", "text", weight=3.0),
        FieldMatch("city", "exact", weight=0.5),
        FieldMatch("income", "numeric", weight=1.0, tolerance=0.05, relative=True),
        FieldMatch("born", "date", weight=1.0, tolerance=5),
    ],
    threshold=0.85,
)


def test_generator_records_true_clusters_and_keeps_originals_in_place() -> None:
    base = people()
    d = make_duplicates(base, rate=0.2, max_copies=3, fuzz=0.6, seed=11, id_column="id")
    assert (
        d.table.slice(0, base.num_rows)
        .drop(["name", "income", "born", "city"])
        .equals(base.drop(["name", "income", "born", "city"]))
    )
    assert d.table.slice(0, base.num_rows).equals(base)
    assert d.labels.shape == (d.table.num_rows,)
    assert d.labels[: base.num_rows].tolist() == list(range(base.num_rows))
    assert len(d.clusters) == round(base.num_rows * 0.2)
    for members in d.clusters:
        assert members == sorted(members) and 2 <= len(members) <= 4
        assert len({int(d.labels[m]) for m in members}) == 1
    assert d.true_pairs == sum(len(m) * (len(m) - 1) // 2 for m in d.clusters)
    ids = d.table["id"].to_pylist()
    assert len(set(ids)) == len(ids)  # copies get fresh ids


def test_generator_is_deterministic_per_seed_and_seeds_differ() -> None:
    a = make_duplicates(people(), rate=0.1, seed=3)
    b = make_duplicates(people(), rate=0.1, seed=3)
    c = make_duplicates(people(), rate=0.1, seed=4)
    assert a.table.equals(b.table) and a.clusters == b.clusters
    assert not a.table.equals(c.table)


def test_generator_validation_and_zero_rate() -> None:
    assert make_duplicates(people(10), rate=0.0, seed=1).clusters == []
    for bad in (dict(rate=1.5), dict(rate=-0.1), dict(max_copies=0), dict(fuzz=2.0)):
        with pytest.raises(ValueError):
            make_duplicates(people(10), seed=1, **bad)
    with pytest.raises(ValueError, match="no column"):
        make_duplicates(people(10), seed=1, id_column="zzz")


def test_precision_recall_f1_on_known_clusters_meet_fixed_thresholds() -> None:
    d = make_duplicates(people(), rate=0.25, max_copies=2, fuzz=0.5, seed=21, id_column="id")
    res = resolve(d.table, CONFIG)
    m = pair_metrics(res.labels, d.labels)
    assert m.precision >= 0.95, m
    assert m.recall >= 0.85, m
    assert m.f1 >= 0.90, m
    n = d.table.num_rows
    assert res.stats["candidate_pairs"] < 0.15 * n * (n - 1) / 2  # blocking prunes 85% of the pairs


def test_blocking_recall_is_reported_against_truth() -> None:
    d = make_duplicates(people(), rate=0.25, seed=2, fuzz=0.5, id_column="id")
    res = resolve(d.table, CONFIG)
    assert res.blocking_recall(d.labels) >= 0.95


def test_resolve_is_deterministic_and_golden_collapses_clusters() -> None:
    d = make_duplicates(people(200), rate=0.3, seed=8, fuzz=0.4, id_column="id")
    cfg = ResolveConfig(
        CONFIG.blocks,
        CONFIG.fields,
        CONFIG.threshold,
        survivorship={"name": Survivorship("longest")},
    )
    a, b = resolve(d.table, cfg), resolve(d.table, cfg)
    assert a.labels.tolist() == b.labels.tolist() and a.golden.table.equals(b.golden.table)
    assert a.golden.table.num_rows == len(set(a.labels.tolist()))
    assert a.golden.table.num_rows < d.table.num_rows


def test_perfect_and_degenerate_metrics() -> None:
    t = np.array([0, 0, 2, 2, 4])
    assert pair_metrics(t, t).f1 == 1.0
    m = pair_metrics(np.arange(5), t)  # nothing merged
    assert (m.tp, m.fp, m.fn, m.precision, m.recall, m.f1) == (0, 0, 2, 1.0, 0.0, 0.0)
    m = pair_metrics(np.zeros(5, dtype=np.int64), t)  # everything merged
    assert (m.tp, m.fp, m.fn) == (2, 8, 0) and m.recall == 1.0
    assert pair_metrics(np.arange(3), np.arange(3)).f1 == 1.0  # no true pairs, none predicted
    with pytest.raises(ValueError, match="same length"):
        pair_metrics(np.arange(2), np.arange(3))


def test_truth_file_round_trip_and_version_check(tmp_path) -> None:
    d = make_duplicates(people(50), rate=0.2, seed=1)
    p = write_truth(tmp_path / "truth.json", d)
    labels = read_truth(p, rows=d.table.num_rows)
    assert labels.tolist() == d.labels.tolist()
    bad = tmp_path / "bad.json"
    bad.write_text('{"format": "shape-duplicate-truth", "version": 99, "rows": 1, "clusters": []}')
    with pytest.raises(ValueError, match="newer"):
        read_truth(bad)
    bad.write_text('{"format": "other", "version": 1}')
    with pytest.raises(ValueError, match="not a duplicate-truth"):
        read_truth(bad)
    with pytest.raises(ValueError, match="rows"):
        read_truth(p, rows=3)


def test_config_dict_round_trip() -> None:
    again = ResolveConfig.from_dict(CONFIG.to_dict())
    assert again.to_dict() == CONFIG.to_dict()
    assert CONFIG.to_dict()["format"] == "shape-resolve-config" and CONFIG.to_dict()["version"] == 1
    with pytest.raises(ValueError, match="threshold"):
        ResolveConfig(CONFIG.blocks, CONFIG.fields, threshold=1.5)
    with pytest.raises(ValueError, match="cluster method"):
        ResolveConfig(CONFIG.blocks, CONFIG.fields, cluster="bogus")
    with pytest.raises(ValueError, match="newer"):
        ResolveConfig.from_dict({**CONFIG.to_dict(), "version": 2})
