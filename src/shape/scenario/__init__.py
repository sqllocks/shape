"""Scenario packs and the Generation Spec Language (P6-14).

``PackLoader`` reads a pack, ``PackValidator`` checks it against a domain, ``PackRunner`` runs it
and writes a run manifest; ``GSLParser`` reads a generation spec that points at a pack. Nothing
heavy loads at import time.
"""

from shape.scenario.gsl import GenerationSpec, GSLParser
from shape.scenario.loader import PackError, PackLoader, ScenarioPack
from shape.scenario.manifest import ManifestBuilder, RunManifest
from shape.scenario.resolve import spec_domain, spec_pack, validate_spec
from shape.scenario.runner import PackRunner, RunResult
from shape.scenario.validator import PackValidationResult, PackValidator

__all__ = [
    "GSLParser",
    "GenerationSpec",
    "ManifestBuilder",
    "PackError",
    "PackLoader",
    "PackRunner",
    "PackValidationResult",
    "PackValidator",
    "RunManifest",
    "RunResult",
    "ScenarioPack",
    "spec_domain",
    "spec_pack",
    "validate_spec",
]
