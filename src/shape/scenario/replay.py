"""Replay a run from its manifest: regenerate the dataset and check its id.

``replay`` takes a run manifest (``<run_id>_manifest.json``) and the pack or spec the run used,
regenerates the run into a scratch directory with the manifest's domain, scale and seed, and
compares the dataset id of the result with the recorded one. A run is only replayable when the
manifest records a dataset id, names the same pack, and, for a spec, the spec file has not changed
(its SHA-256 must equal ``spec_hash``). A match means the output tables hold the same content; the
files written for the run are not touched.

The reproducibility tuple of the manifest is compared with the one of this environment and every
difference is reported (``shape_version``, ``kernel``, ``platform`` ...). A difference does not fail
a match, and on a mismatch it is the first place to look.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shape.reproducibility import reproducibility_tuple
from shape.scenario.manifest import RunManifest, hash_file


@dataclass
class ReplayResult:
    """The outcome of a replay: ``match``, the two ids, and the tuple fields that differ."""

    run_id: str
    match: bool
    expected: str
    actual: str
    differences: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "match": self.match,
            "expected": self.expected,
            "actual": self.actual,
            "differences": self.differences,
        }


def replay(
    manifest: RunManifest,
    pack: Any,
    domain: Any,
    *,
    spec: Any | None = None,
    spec_path: str | Path | None = None,
) -> ReplayResult:
    """Regenerate the run ``manifest`` describes and compare dataset ids. Raises ``ValueError``
    when the run cannot be replayed (no recorded dataset id, another pack, a changed spec)."""
    from shape.scenario.runner import PackRunner
    from shape.scenario.validator import domain_name_of

    if not manifest.dataset_id:
        raise ValueError(
            f"run {manifest.run_id} has no dataset id (it was recorded before dataset ids "
            "existed): there is nothing to check a replay against"
        )
    if pack.id != manifest.pack_id:
        raise ValueError(
            f"the run used pack {manifest.pack_id!r}, but the target is pack {pack.id!r}"
        )
    name = domain_name_of(domain)
    if manifest.domain and name != manifest.domain:
        raise ValueError(f"the run used domain {manifest.domain!r}, but the target gives {name!r}")
    if manifest.spec_hash:
        if spec is None or spec_path is None:
            raise ValueError(
                "the run used a generation spec (spec_hash is set): give the spec file, not a pack"
            )
        if hash_file(Path(spec_path)) != manifest.spec_hash:
            raise ValueError(
                f"the spec file {spec_path} has changed since the run (its SHA-256 differs from "
                "the manifest's spec_hash), so the run cannot be replayed from it"
            )
    elif spec is not None:
        raise ValueError("the run used no generation spec, but the target is a spec")
    with tempfile.TemporaryDirectory(prefix="shape-replay-") as scratch:
        result = PackRunner().run(
            pack, domain, manifest.scale, manifest.seed, Path(scratch), spec=spec
        )
    if result.manifest is None or not result.manifest.dataset_id:
        raise ValueError("replay could not regenerate the run: " + "; ".join(result.errors))
    actual = result.manifest.dataset_id
    now = reproducibility_tuple(manifest.seed, manifest.scale)
    differences = [
        {"field": key, "recorded": recorded, "current": now[key]}
        for key, recorded in manifest.reproducibility.items()
        if key in now and now[key] != recorded
    ]
    return ReplayResult(
        manifest.run_id, actual == manifest.dataset_id, manifest.dataset_id, actual, differences
    )
