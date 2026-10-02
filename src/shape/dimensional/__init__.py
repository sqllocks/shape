"""Dimensional transforms: a set of related tables to a star schema, or to a CDM folder (P6-06).

``star_transform`` turns normalised tables into dimension and fact tables with surrogate keys and a
date dimension, driven by a :class:`StarMap`; ``write_cdm_folder`` writes tables as a Common Data
Model folder (``model.json`` plus one data file per entity). Both work on Arrow tables and import
nothing heavy until called (T-18).
"""

from shape.dimensional.cdm import cdm_type, entity_name, model_document, write_cdm_folder
from shape.dimensional.star import StarMap, StarResult, star_transform

__all__ = [
    "StarMap",
    "StarResult",
    "cdm_type",
    "entity_name",
    "model_document",
    "star_transform",
    "write_cdm_folder",
]
