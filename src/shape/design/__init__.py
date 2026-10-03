"""Deterministic schema design (W5-02): a structured design input to a 3NF, star or snowflake
schema, as DDL, with a lint report. See ``docs/DESIGN.md``.

Stable interface: :class:`DesignInput`, :func:`load_design`, :func:`design_input_schema`,
:class:`DesignError`.
"""

from shape.design.model import DesignError, DesignInput, design_input_schema, load_design

__all__ = ["DesignError", "DesignInput", "design_input_schema", "load_design"]
