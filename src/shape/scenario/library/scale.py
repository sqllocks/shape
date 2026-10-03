"""Scale names for scenarios and test datasets: a preset of the domain, or ``tiny``.

``tiny`` is for tests that want a few rows fast: 100 rows per table, unless the domain defines a
preset of that name. Any other name must be a preset of the schema.
"""

from __future__ import annotations

from shape.errors import ShapeError
from shape.generation.schema import GenSchema

TINY = "tiny"
TINY_ROWS = 100


def resolve_scale(schema: GenSchema, scale: str | None) -> tuple[str | None, dict[str, int] | None]:
    """``(preset for the engine, row counts for the engine)`` for a scale name.

    ``None`` is the schema's own default scale. A name that is neither a preset of ``schema`` nor
    ``tiny`` raises :class:`~shape.errors.ShapeError` that lists the choices.
    """
    if scale is None:
        return None, None
    presets = schema.generation.scales
    if scale in presets:
        return scale, None
    if scale == TINY:
        return None, {name: TINY_ROWS for name in schema.tables}
    choices = ", ".join([*presets, TINY]) if presets else TINY
    raise ShapeError(f"unknown scale {scale!r}; the scales are: {choices}")
