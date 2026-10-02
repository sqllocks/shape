"""Shape: Shape as Code for data behavior.

Names are loaded on first use (PEP 562), so ``import shape`` costs almost nothing and
``shape --version`` starts in a few milliseconds; ``shape.profile`` and the rest import their
modules when they are first touched.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

__version__ = "0.9.0"

from shape import _process  # noqa: E402

_process.configure()  # Arrow's allocator for the whole process, in this one place (see _process)

if TYPE_CHECKING:
    from .api import certify as certify
    from .api import check as check
    from .api import diff as diff
    from .api import generate as generate
    from .api import load as load
    from .api import plan as plan
    from .api import profile as profile
    from .api import query as query
    from .api import save as save
    from .api import timeline as timeline
    from .api import view as view
    from .model import Evidence as Evidence
    from .model import Provenance as Provenance
    from .model import Shape as Shape
    from .model import ShapeBuilder as ShapeBuilder
    from .security import Sensitivity as Sensitivity
    from .types import FieldType as FieldType
    from .types import LogicalType as LogicalType
    from .types import from_arrow_type as from_arrow_type
    from .types import schema_from_arrow as schema_from_arrow

_API = (
    "certify",
    "check",
    "generate",
    "load",
    "plan",
    "profile",
    "query",
    "save",
    "timeline",
    "view",
)
_LAZY: dict[str, tuple[str, str | None]] = {
    **{name: ("shape.api", name) for name in _API},
    "diff": ("shape.diff", None),  # the package, callable as shape.diff(before, after)
    "Evidence": ("shape.model", "Evidence"),
    "Provenance": ("shape.model", "Provenance"),
    "Shape": ("shape.model", "Shape"),
    "ShapeBuilder": ("shape.model", "ShapeBuilder"),
    "Sensitivity": ("shape.security", "Sensitivity"),
    "FieldType": ("shape.types", "FieldType"),
    "LogicalType": ("shape.types", "LogicalType"),
    "from_arrow_type": ("shape.types", "from_arrow_type"),
    "schema_from_arrow": ("shape.types", "schema_from_arrow"),
    "_kernel": ("shape._kernel", None),  # the native extension
}

__all__ = [
    "Evidence",
    "FieldType",
    "LogicalType",
    "Provenance",
    "Sensitivity",
    "Shape",
    "ShapeBuilder",
    "from_arrow_type",
    "schema_from_arrow",
    "profile",
    "save",
    "load",
    "diff",
    "generate",
    "timeline",
    "view",
    "query",
    "certify",
    "plan",
    "check",
]


def __getattr__(name: str) -> Any:
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module 'shape' has no attribute {name!r}")
    module_name, attr = target
    module = importlib.import_module(module_name)
    value = module if attr is None else getattr(module, attr)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY})
