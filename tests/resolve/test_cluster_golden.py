"""W3-09 (#72): clustering and golden records with survivorship rules."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest

from shape.resolve.cluster import center_clusters, connected_components
from shape.resolve.golden import Survivorship, build_golden


def test_connected_components_label_by_smallest_member() -> None:
    labels = connected_components(6, np.array([[4, 5], [1, 2], [2, 3]]))
    assert labels.tolist() == [0, 1, 1, 1, 4, 4]
    assert connected_components(3, np.empty((0, 2), dtype=np.int64)).tolist() == [0, 1, 2]
    assert connected_components(0, np.empty((0, 2), dtype=np.int64)).tolist() == []


def test_connected_components_chain_but_center_does_not() -> None:
    pairs = np.array([[0, 1], [1, 2], [2, 3]])
    scores = np.array([0.99, 0.95, 0.90])
    assert connected_components(4, pairs).tolist() == [0, 0, 0, 0]
    got = center_clusters(4, pairs, scores).tolist()
    assert got[0] == got[1] and got[2] == got[3] and got[0] != got[2]


def test_center_clustering_is_deterministic_under_ties() -> None:
    pairs = np.array([[0, 1], [1, 2], [0, 2]])
    scores = np.array([0.9, 0.9, 0.9])
    a = center_clusters(3, pairs, scores).tolist()
    assert a == center_clusters(3, pairs[::-1], scores[::-1]).tolist()


def people() -> pa.Table:
    return pa.table(
        {
            "name": ["Jon Smith", "John Smith", "John Smith", "Mary Jones"],
            "phone": [None, "555-1", "555-2", "555-9"],
            "updated": [1, 3, 2, 1],
            "source": ["web", "crm", "web", "crm"],
            "city": ["x", None, "yy", "z"],
        }
    )


def test_golden_record_survivorship_rules_per_column() -> None:
    labels = np.array([0, 0, 0, 3])
    rules = {
        "name": Survivorship("most_common"),
        "phone": Survivorship("most_recent", by="updated"),
        "city": Survivorship("longest"),
        "source": Survivorship("priority", by="source", order=("crm", "web")),
        "updated": Survivorship("max"),
    }
    g = build_golden(people(), labels, rules)
    rows = g.table.to_pylist()
    assert g.table.num_rows == 2
    assert rows[0]["name"] == "John Smith"
    assert rows[0]["phone"] == "555-1"  # newest non-null (updated=3)
    assert rows[0]["city"] == "yy" and rows[0]["source"] == "crm" and rows[0]["updated"] == 3
    assert rows[0]["_cluster_id"] == 0 and rows[0]["_cluster_size"] == 3
    assert rows[1]["name"] == "Mary Jones" and rows[1]["_cluster_size"] == 1


def test_golden_lineage_names_the_surviving_row_per_column() -> None:
    g = build_golden(
        people(), np.array([0, 0, 0, 3]), {"phone": Survivorship("most_recent", by="updated")}
    )
    lin = g.lineage[0]
    assert lin["cluster"] == 0 and lin["members"] == [0, 1, 2]
    assert lin["survivors"]["phone"] == 1
    assert lin["survivors"]["name"] == 0  # default rule: first non-null in row order


def test_default_rules_first_min_last_shortest_and_null_clusters() -> None:
    t = pa.table({"a": [None, None, "q"], "b": [5, 2, 9], "c": ["ab", "a", "abc"]})
    labels = np.array([0, 0, 2])
    g = build_golden(
        t,
        labels,
        {"b": Survivorship("min"), "c": Survivorship("shortest"), "a": Survivorship("last")},
    )
    r = g.table.to_pylist()
    assert r[0] == {"a": None, "b": 2, "c": "a", "_cluster_id": 0, "_cluster_size": 2}
    assert r[1]["a"] == "q"


def test_survivorship_validation() -> None:
    with pytest.raises(ValueError, match="unknown survivorship"):
        Survivorship("bogus")
    with pytest.raises(ValueError, match="by"):
        Survivorship("most_recent")
    with pytest.raises(ValueError, match="order"):
        Survivorship("priority", by="source")
    with pytest.raises(ValueError, match="no column"):
        build_golden(people(), np.zeros(4, dtype=np.int64), {"zzz": Survivorship("first")})
    with pytest.raises(ValueError, match="one label per row"):
        build_golden(people(), np.zeros(2, dtype=np.int64), {})
