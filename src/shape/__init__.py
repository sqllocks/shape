"""Shape: Shape as Code for data behavior."""

from .api import (
    certify as certify,
)
from .api import (
    check as check,
)
from .api import (
    diff as diff,
)
from .api import (
    generate as generate,
)
from .api import (
    load as load,
)
from .api import (
    plan as plan,
)
from .api import (
    profile as profile,
)
from .api import (
    query as query,
)
from .api import (
    save as save,
)
from .api import (
    timeline as timeline,
)
from .api import (
    view as view,
)
from .model import (
    Evidence as Evidence,
)
from .model import (
    Provenance as Provenance,
)
from .model import (
    Shape as Shape,
)
from .model import (
    ShapeBuilder as ShapeBuilder,
)
from .security import Sensitivity as Sensitivity
from .types import (
    FieldType as FieldType,
)
from .types import (
    LogicalType as LogicalType,
)
from .types import (
    from_arrow_type as from_arrow_type,
)
from .types import (
    schema_from_arrow as schema_from_arrow,
)

__version__ = "0.9.0"
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


def __getattr__(name: str) -> object:
    """``shape._kernel`` is the native extension; import it on first access so that
    ``import shape; shape._kernel.version()`` works without paying for it at import time."""
    if name == "_kernel":
        import importlib

        return importlib.import_module("shape._kernel")
    raise AttributeError(f"module 'shape' has no attribute {name!r}")
