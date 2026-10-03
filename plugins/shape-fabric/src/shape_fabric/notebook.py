"""Fabric notebooks: ``shape fabric notebook`` and ``deploy-notebook``.

:func:`generate_notebook` builds a ready-to-run ``.ipynb`` (as a dict) that installs Shape,
generates a domain and writes it to the default Lakehouse, to CSV files, or shows a sample.
:func:`item_definition` is the body the Fabric Items API takes to create that notebook in a
workspace: the notebook content part is named ``notebook-content.ipynb`` (the format the
definition declares) next to a ``.platform`` part that carries the item's display name.
"""

from __future__ import annotations

import base64
import json
import re
import uuid
from pathlib import Path
from typing import Any

OUTPUT_TARGETS = ("lakehouse", "display", "csv")
PLATFORM_SCHEMA = (
    "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/"
    "platformProperties/2.0.0/schema.json"
)


# The values that go into notebook code: a domain is a plain name (an installed domain plugin), a
# version is a PEP 440 version, a seed an integer. Anything else could become code in a cell.
_DOMAIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+!_-]*")


def _checked(domain: Any, seed: Any, version: Any) -> None:
    if not isinstance(domain, str) or not _DOMAIN.fullmatch(domain):
        raise ValueError(f"not a domain name: {domain!r} (an installed domain, such as 'retail')")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError(f"the seed must be an integer, not {seed!r}")
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise ValueError(f"not a package version: {version!r}")


def _version() -> str:
    from shape import __version__

    return str(__version__)


def generate_notebook(
    domain: str,
    scale: str = "small",
    seed: int = 42,
    output_target: str = "lakehouse",
    *,
    version: str | None = None,
) -> dict[str, Any]:
    """A Fabric notebook that generates ``domain`` at ``scale`` with ``seed`` (an ``.ipynb`` dict).

    ``output_target`` is ``lakehouse`` (Parquet files under the default Lakehouse's ``Files``),
    ``csv`` (CSV files in the working folder) or ``display`` (the first rows of each table).
    """
    if output_target not in OUTPUT_TARGETS:
        raise ValueError(
            f"unknown notebook target {output_target!r}; choose one of {', '.join(OUTPUT_TARGETS)}"
        )
    ver = version or _version()
    _checked(domain, seed, ver)
    cells: list[dict[str, Any]] = [
        _markdown(
            f"# Shape data generation: {domain.title()}\n\n"
            f"This notebook generates synthetic data with **Shape v{ver}**.\n\n"
            f"- **Domain**: {domain}\n- **Scale**: {scale}\n- **Seed**: {seed}\n"
            f"- **Output**: {output_target}"
        ),
        _code(f"%pip install sqllocks-shape=={ver} sqllocks-shape-domains=={ver} -q"),
        _code(
            "import shape\n\n"
            f"result = shape.generate({domain!r}, scale={scale!r}, seed={seed})\n\n"
            "for name, table in result.tables.items():\n"
            '    print(f"{name:<28}{table.num_rows:>12,} rows{table.num_columns:>4} columns")\n'
            'print(f"{sum(result.row_counts.values()):,} rows in {len(result.tables)} tables")\n\n'
            "problems = result.verify_integrity()\n"
            "if problems:\n"
            '    print("Foreign-key integrity problems:")\n'
            "    for problem in problems:\n"
            '        print(f"  WARNING: {problem}")\n'
            "else:\n"
            '    print("Foreign-key integrity: PASS")'
        ),
    ]
    if output_target == "lakehouse":
        cells.append(
            _markdown(
                "## Write to the Lakehouse\n\nWrites every table as a Parquet file under the "
                "default Lakehouse's `Files` folder. Attach a Lakehouse to this notebook first."
            )
        )
        cells.append(
            _code(
                "import os\n"
                "import pyarrow.parquet as pq\n\n"
                "lakehouse_path = os.environ.get(\n"
                "    'LAKEHOUSE_FILES_PATH', '/lakehouse/default/Files'\n"
                ")\n"
                f'output_dir = f"{{lakehouse_path}}/shape/{domain}"\n'
                "os.makedirs(output_dir, exist_ok=True)\n\n"
                "paths = []\n"
                "for name, table in result.tables.items():\n"
                '    path = f"{output_dir}/{name}.parquet"\n'
                "    pq.write_table(table, path)\n"
                "    paths.append(path)\n"
                '    print(f"  Written: {path}")\n\n'
                'print(f"{len(paths)} tables written to {output_dir}")'
            )
        )
    elif output_target == "csv":
        cells.append(
            _code(
                "import os\n"
                "import pyarrow.csv as pacsv\n\n"
                f"output_dir = './shape_{domain}'\n"
                "os.makedirs(output_dir, exist_ok=True)\n"
                "for name, table in result.tables.items():\n"
                '    path = f"{output_dir}/{name}.csv"\n'
                "    pacsv.write_csv(table, path)\n"
                '    print(f"  Written: {path}")'
            )
        )
    else:
        cells.append(_markdown("## Sample data"))
        cells.append(
            _code(
                "for name in list(result.tables)[:5]:\n"
                '    print(f"\\n--- {name} ---")\n'
                "    display(result.tables[name].slice(0, 5).to_pandas())"
            )
        )
    return _structure(cells)


def save_notebook(notebook: dict[str, Any], output_path: str | Path) -> Path:
    """Write ``notebook`` to ``output_path`` (parent folders are created); return the path."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(notebook, fh, indent=1)
    return path


def item_definition(notebook: dict[str, Any], display_name: str) -> dict[str, Any]:
    """The Fabric Items API body that creates ``notebook`` as a Notebook item."""

    def part(path: str, payload: str) -> dict[str, str]:
        return {
            "path": path,
            "payload": base64.b64encode(payload.encode("utf-8")).decode("ascii"),
            "payloadType": "InlineBase64",
        }

    platform = {
        "$schema": PLATFORM_SCHEMA,
        "metadata": {"type": "Notebook", "displayName": display_name},
        # stable per name, so deploying the same notebook twice sends the same definition
        "config": {
            "version": "2.0",
            "logicalId": str(uuid.uuid5(uuid.NAMESPACE_URL, f"shape:notebook:{display_name}")),
        },
    }
    return {
        "displayName": display_name,
        "type": "Notebook",
        "definition": {
            "format": "ipynb",
            "parts": [
                part("notebook-content.ipynb", json.dumps(notebook)),
                part(".platform", json.dumps(platform)),
            ],
        },
    }


def _structure(cells: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": "3.11.0"},
        },
        "cells": cells,
    }


def _code(source: str) -> dict[str, Any]:
    return {
        "cell_type": "code",
        "metadata": {},
        "source": source.splitlines(keepends=True),
        "outputs": [],
        "execution_count": None,
    }


def _markdown(source: str) -> dict[str, Any]:
    return {"cell_type": "markdown", "metadata": {}, "source": source.splitlines(keepends=True)}
