"""Duplicate detection and entity resolution with known synthetic clusters (W3-09).

``resolve`` blocks, matches, clusters and builds golden records; ``make_duplicates`` plants
duplicates and records the true clusters; ``pair_metrics`` scores one against the other.
"""

from shape.resolve.api import ResolveConfig, ResolveResult, resolve
from shape.resolve.blocking import BlockRule, candidate_pairs
from shape.resolve.cluster import center_clusters, connected_components
from shape.resolve.golden import Golden, Survivorship, build_golden
from shape.resolve.matching import FieldMatch, score_pairs
from shape.resolve.metrics import PairMetrics, pair_metrics
from shape.resolve.synth import SyntheticDuplicates, make_duplicates, read_truth, write_truth

__all__ = [
    "BlockRule",
    "FieldMatch",
    "Golden",
    "PairMetrics",
    "ResolveConfig",
    "ResolveResult",
    "Survivorship",
    "SyntheticDuplicates",
    "build_golden",
    "candidate_pairs",
    "center_clusters",
    "connected_components",
    "make_duplicates",
    "pair_metrics",
    "read_truth",
    "resolve",
    "score_pairs",
    "write_truth",
]
