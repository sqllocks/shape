"""The ``shape.transforms`` built-ins for dimensional output (P6-06): ``star`` and ``cdm``.

``star`` reshapes tables into dimensions, a date dimension and facts as a ``StarMap`` says
(``apply(tables, map=DOCUMENT)``); ``cdm`` renames tables to their CDM entities
(``apply(tables, entities={"table": "Entity"})``), the tables that ``shape transform cdm`` writes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

SHAPE_API = "1.0"


class StarTransform:
    name = "star"

    def apply(self, tables: Mapping[str, pa.Table], **options: Any) -> dict[str, pa.Table]:
        from shape.dimensional import StarMap, star_transform

        document = options.get("map")
        if not isinstance(document, Mapping):
            raise ValueError("the star transform needs a star map: apply(tables, map={...})")
        return star_transform(tables, StarMap.from_dict(document)).tables()


class CdmTransform:
    name = "cdm"

    def apply(self, tables: Mapping[str, pa.Table], **options: Any) -> dict[str, pa.Table]:
        from shape.dimensional.cdm import entity_name

        names = options.get("entities")
        out: dict[str, pa.Table] = {}
        for table, data in tables.items():
            entity = entity_name(table, names if isinstance(names, Mapping) else None)
            if entity in out:
                raise ValueError(f"two tables become the entity {entity!r}; name one of them")
            out[entity] = data
        return out
