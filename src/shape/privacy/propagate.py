"""Sensitivity propagation across derived fields."""

from __future__ import annotations

from .classification import ClassificationTaxonomy


def derived_classification(input_levels, explicit=None, taxonomy=None):
    """Join inherited and explicit sensitivity levels without lowering classification."""
    t = taxonomy or ClassificationTaxonomy()
    inherited = t.join(*tuple(input_levels)) if input_levels else t.levels[0]
    if explicit is None:
        return inherited
    # never allow an implicit downgrade
    return t.join(inherited, explicit)
