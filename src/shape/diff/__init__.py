"""Shape diffs. The package is also callable: ``shape.diff(before, after)`` compares two
profiles (``shape.contracts.v1.diff``) and works however the package was imported."""

from .core import Delta as Delta
from .core import diff_mapping as diff_mapping
from .core import diff_models as diff_models

__all__ = ["Delta", "diff_mapping", "diff_models"]


from shape._callable import make_callable

make_callable(__name__, "diff")  # shape.diff(before, after) is also the public function
