"""Fabric environment set-up: ``shape fabric setup``.

The Fabric Environment item is what a notebook attaches to for its Python libraries.
:func:`library_spec` is the list of libraries Shape needs; :data:`SETUP_SNIPPET` is the cell to
paste into any notebook instead.
"""

from __future__ import annotations

from typing import Any

DEFAULT_ENVIRONMENT = "shape-env"
DEFAULT_LAKEHOUSE = "shape-lakehouse"


def _version() -> str:
    from shape import __version__

    return str(__version__)


def library_spec(version: str | None = None) -> dict[str, Any]:
    """The libraries a Fabric Environment needs to run Shape."""
    ver = version or _version()
    return {
        "customLibraries": {
            "pypi": [
                {"name": "sqllocks-shape", "version": ver},
                {"name": "sqllocks-shape-domains", "version": ver},
                {"name": "deltalake", "version": ">=0.17.0"},
                {"name": "pyarrow", "version": ">=14.0"},
            ]
        }
    }


def setup_snippet(version: str | None = None) -> str:
    """The cell that installs Shape in a Fabric notebook and runs a small check."""
    ver = version or _version()
    return (
        "# Shape environment setup: run this cell in any Fabric notebook.\n"
        f"%pip install sqllocks-shape=={ver} sqllocks-shape-domains=={ver} -q\n"
        "\n"
        "import shape\n"
        "\n"
        'result = shape.generate("retail", scale="small", seed=42)\n'
        "problems = result.verify_integrity()\n"
        'print(f"Shape v{shape.__version__} installed")\n'
        "rows = sum(result.row_counts.values())\n"
        'print(f"Generated {rows:,} rows in {len(result.tables)} tables")\n'
        'print("Foreign-key integrity:", "PASS" if not problems else f"FAIL ({len(problems)})")'
    )
