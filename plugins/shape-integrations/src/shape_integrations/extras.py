"""The optional libraries behind each adapter, and the error when one is missing.

Importing :mod:`shape_integrations` imports none of them. An adapter calls :func:`require` when
it first needs its library; a command turns :class:`MissingExtraError` into exit code 2.
"""

from __future__ import annotations

import importlib
from types import ModuleType

EXTRAS = ("openlineage", "mlflow", "presidio", "sdmetrics", "ibis")


class MissingExtraError(ImportError):
    """An adapter's third-party library is not installed."""

    def __init__(self, name: str, extra: str) -> None:
        self.name = name
        self.extra = extra
        if extra == "anonymeter":
            message = (
                "The 'anonymeter' extra was withdrawn in 0.9.4 because "
                "Anonymeter 1.x requires NumPy < 1.27 and Shape requires NumPy >= 2. "
                "The Anonymeter report becomes available again when Anonymeter supports NumPy 2."
            )
        else:
            message = (
                f"{name} needs the '{extra}' extra: "
                f"pip install 'sqllocks-shape-integrations[{extra}]'"
            )
        super().__init__(message)


def require(module: str, extra: str, *, name: str | None = None) -> ModuleType:
    """Import ``module`` or raise :class:`MissingExtraError` naming ``extra``.

    A library that is installed but fails inside its own import (a broken dependency) is not
    reported as missing: that error passes through unchanged.
    """
    try:
        return importlib.import_module(module)
    except ModuleNotFoundError as exc:
        top = module.split(".")[0]
        if exc.name is not None and exc.name.split(".")[0] != top:
            raise
        raise MissingExtraError(name or top, extra) from None
