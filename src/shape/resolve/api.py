"""The resolution pipeline: block, match, cluster, build golden records."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]

from shape.resolve.blocking import BlockRule, candidate_pairs
from shape.resolve.cluster import METHODS as CLUSTER_METHODS
from shape.resolve.cluster import center_clusters, connected_components
from shape.resolve.golden import Golden, Survivorship, build_golden
from shape.resolve.matching import FieldMatch, score_pairs

CONFIG_FORMAT = "shape-resolve-config"
CONFIG_VERSION = 1


@dataclass(frozen=True)
class ResolveConfig:
    """Blocking rules, matching fields, a score threshold, a clustering method, survivorship.

    Resolution draws no random numbers: the same table and config give the same clusters.
    """

    blocks: list[BlockRule]
    fields: list[FieldMatch]
    threshold: float = 0.85
    cluster: str = "connected"
    survivorship: Mapping[str, Survivorship] = field(default_factory=dict)
    max_block: int = 500

    def __post_init__(self) -> None:
        if not 0.0 < self.threshold <= 1.0:
            raise ValueError(f"the threshold is above 0 and at most 1, got {self.threshold}")
        if self.cluster not in CLUSTER_METHODS:
            raise ValueError(
                f"unknown cluster method {self.cluster!r}; "
                f"choose one of {', '.join(CLUSTER_METHODS)}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": CONFIG_FORMAT,
            "version": CONFIG_VERSION,
            "blocks": [b.to_dict() for b in self.blocks],
            "fields": [f.to_dict() for f in self.fields],
            "threshold": self.threshold,
            "cluster": self.cluster,
            "survivorship": {k: v.to_dict() for k, v in self.survivorship.items()},
            "max_block": self.max_block,
        }

    @classmethod
    def from_dict(cls, doc: Mapping[str, Any]) -> ResolveConfig:
        if doc.get("format", CONFIG_FORMAT) != CONFIG_FORMAT:
            raise ValueError(f"not a resolve config (format {CONFIG_FORMAT})")
        version = doc.get("version", CONFIG_VERSION)
        if not isinstance(version, int) or version > CONFIG_VERSION:
            raise ValueError(
                f"the config has a newer version ({version}) than this Shape reads "
                f"({CONFIG_VERSION})"
            )
        return cls(
            blocks=[BlockRule(**b) for b in doc.get("blocks", [])],
            fields=[FieldMatch(**f) for f in doc.get("fields", [])],
            threshold=float(doc.get("threshold", 0.85)),
            cluster=str(doc.get("cluster", "connected")),
            survivorship={k: Survivorship(**v) for k, v in doc.get("survivorship", {}).items()},
            max_block=int(doc.get("max_block", 500)),
        )


@dataclass(frozen=True)
class ResolveResult:
    """Cluster labels (smallest row of each cluster), the matched pairs and their scores, the
    candidate pairs, golden records and counts."""

    labels: npt.NDArray[np.int64]
    matched: npt.NDArray[np.int64]
    scores: npt.NDArray[np.float64]
    candidates: npt.NDArray[np.int64]
    golden: Golden
    stats: dict[str, Any]

    def clusters(self) -> list[list[int]]:
        """The clusters of two or more rows, each sorted, ordered by smallest member."""
        groups: dict[int, list[int]] = {}
        for row, lab in enumerate(self.labels.tolist()):
            groups.setdefault(int(lab), []).append(row)
        return [m for _, m in sorted(groups.items()) if len(m) > 1]

    def blocking_recall(self, truth: npt.NDArray[np.int64]) -> float:
        """The share of true pairs that blocking kept as candidates (1.0 when none exist)."""
        t = np.asarray(truth)
        _, inv = np.unique(t, return_inverse=True)
        counts = np.bincount(inv)
        total = int((counts * (counts - 1) // 2).sum())
        if total == 0:
            return 1.0
        i, j = self.candidates[:, 0], self.candidates[:, 1]
        return float(np.sum(t[i] == t[j])) / total


def resolve(table: pa.Table, config: ResolveConfig) -> ResolveResult:
    """Find the duplicate entities of ``table`` and build one golden record per entity."""
    n = table.num_rows
    candidates, blocking = candidate_pairs(table, config.blocks, max_block=config.max_block)
    result = score_pairs(table, candidates, config.fields)
    keep = result.scores >= config.threshold
    matched, scores = candidates[keep], result.scores[keep]
    if config.cluster == "connected":
        labels = connected_components(n, matched)
    else:
        labels = center_clusters(n, matched, scores)
    golden = build_golden(table, labels, config.survivorship)
    sizes = np.bincount(labels, minlength=n) if n else np.empty(0, np.int64)
    stats: dict[str, Any] = {
        "rows": n,
        "blocking": blocking,
        "candidate_pairs": int(len(candidates)),
        "matched_pairs": int(len(matched)),
        "clusters": int(golden.table.num_rows),
        "duplicate_clusters": int((sizes > 1).sum()),
        "records_merged": int(n - golden.table.num_rows),
    }
    return ResolveResult(labels, matched, scores, candidates, golden, stats)
