"""Fidelity tiers 1 to 3: checks beyond the base report (``shape fidelity --tier N``).

* tier 1 (:mod:`shape.fidelity.tier1`): mixture fits, conditional profiles, the adversarial
  score, temporal profiles and periodicity;
* tier 2 (:mod:`shape.fidelity.tier2`): format preservation, string similarity, cardinality and
  anomaly-rate checks;
* tier 3 (:mod:`shape.fidelity.tier3`): the Chow-Liu dependency tree, drift (PSI, KS, chi-squared)
  and bootstrap resampling. Experimental.

Differential privacy is :mod:`shape.privacy.dp`; the optional CTGAN model is the
``sqllocks-shape-ctgan`` plugin. Names load on first use, so importing this package imports no
numerical library.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "Tier1Profiler": "tier1",
    "Tier1Profile": "tier1",
    "run_tier2": "tier2",
    "Tier2Report": "tier2",
    "ChowLiuTree": "tier3",
    "compare_trees": "tier3",
    "DriftMonitor": "tier3",
    "psi_report": "tier3",
    "population_stability_index": "tier3",
    "bootstrap_table": "tier3",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module 'shape.fidelity' has no attribute {name!r}")
    return getattr(import_module(f"{__name__}.{module}"), name)
