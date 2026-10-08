"""``NotebookGenerator``: a Fabric notebook (``.ipynb``) that runs one demo scenario.

The notebook installs Shape (the same ``%pip`` line as the other Shape notebooks, with the Fabric
plugin, from the wheels uploaded to ``builtin``) and runs the scenario through
:func:`shape.demo.demo_run`. Cell ids are derived from the scenario, mode and position, so
generating the same notebook twice gives identical files.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from shape.demo.catalog import ScenarioMeta
from shape.demo.errors import DemoError
from shape.demo.params import MODES

_NAMESPACE = uuid.UUID("5d0f6a4e-3b64-4f7e-9d0c-2b1a7c9e6f10")


def _version() -> str:
    from shape import __version__

    return str(__version__)


def install_line(version: str) -> str:
    """The ``%pip`` cell: the same packages as the other Shape notebooks, plus the Fabric plugin,
    each installed by the file path of the wheel uploaded to the notebook's built-in resources.
    Never by name and version: PyPI has an older package of the same name and version, which a
    requirement by name can resolve to even with ``--find-links``. The Fabric plugin's Eventstream
    and SQL targets need the Event Hubs and SQL Server plugins, so their wheels are listed too."""
    # the wheel file names of the distributions (a wheel name spells "-" as "_")
    dists = (
        "sqllocks-shape",
        "sqllocks-shape-domains",
        "sqllocks-shape-fabric",
        "sqllocks-shape-eventhubs",
        "sqllocks-shape-sqlserver",
    )
    wheels = [f"{d.replace('-', '_')}-{version}-py3-none-any.whl" for d in dists]
    return (
        "# Upload the wheels to this notebook's built-in resources folder (Resources > builtin):\n"
        + "".join(f"#   {w}\n" for w in wheels)
        + "# They are installed by file path, so a package of the same name and version on PyPI\n"
        "# can never be installed instead. numpy 2 replaces the runtime's numpy 1.26.4, and\n"
        "# pandas a 2.x built for numpy 2 (the runtime's 2.1.4 cannot load with it): pip prints\n"
        "# a dependency conflict for the runtime's matplotlib 3.8.2 (expected; not used here).\n"
        '%pip install "numpy>=2,<3" "pandas>=2.2.2,<3" '
        + " ".join(f"builtin/{w}" for w in wheels)
        + " -q"
    )


def _lines(source: str) -> list[str]:
    return source.splitlines(keepends=True)


class NotebookGenerator:
    def generate(
        self, scenario: ScenarioMeta, mode: str = "inference", output_path: Path | None = None
    ) -> Path:
        notebook = self.build(scenario, mode)
        out = output_path or Path(f"shape_{scenario.name}_{mode}.ipynb")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(notebook, indent=2) + "\n", encoding="utf-8", newline="\n")
        return out

    def build(self, scenario: ScenarioMeta, mode: str = "inference") -> dict[str, Any]:
        if mode not in MODES:
            raise DemoError(f"unknown mode {mode!r}; the modes are: {', '.join(MODES)}")
        if mode not in scenario.supported_modes:
            raise DemoError(
                f"scenario {scenario.name!r} does not support mode {mode!r}; "
                f"it supports: {', '.join(scenario.supported_modes)}"
            )
        cells = self._cells(scenario, mode)
        for index, cell in enumerate(cells):
            cell["id"] = str(uuid.uuid5(_NAMESPACE, f"{scenario.name}:{mode}:{index}"))
        return {
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {
                "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                "language_info": {"name": "python", "version": "3.11.0"},
            },
            "cells": cells,
        }

    # ---- cells ------------------------------------------------------------------------------

    @staticmethod
    def _markdown(source: str) -> dict[str, Any]:
        return {"cell_type": "markdown", "metadata": {}, "source": _lines(source)}

    @staticmethod
    def _code(source: str) -> dict[str, Any]:
        return {
            "cell_type": "code",
            "metadata": {},
            "source": _lines(source),
            "outputs": [],
            "execution_count": None,
        }

    def _params(
        self,
        scenario: ScenarioMeta,
        mode: str,
        domain: str | None,
        rows: str,
        *extra: tuple[str, str],
    ) -> dict[str, Any]:
        lines = [f"    scenario={scenario.name!r},", f"    mode={mode!r},"]
        if domain is not None:
            lines.append(f"    domain={domain!r},")
        lines.append(f"    rows={rows},")
        lines += [
            f"    {key}={value}" if "#" in value else f"    {key}={value}," for key, value in extra
        ]
        lines.append("    seed=SEED,")
        return self._code("params = dict(\n" + "\n".join(lines) + "\n)")

    def _cells(self, scenario: ScenarioMeta, mode: str) -> list[dict[str, Any]]:
        title = scenario.name.replace("_", " ").title()
        cells = [
            self._markdown(
                f"# Shape Demo — {title} ({mode})\n\n> {scenario.description}\n\n"
                f"**Domains:** {', '.join(scenario.domains)}  \n**Mode:** {mode}  "
            ),
            self._code(install_line(_version())),
            self._code("from shape.demo import demo_report, demo_run\n\nSEED = 42"),
        ]
        # A scenario of one domain names it; a composite uses its own domains.
        domain = scenario.domains[0] if len(scenario.domains) == 1 else None
        if mode == "inference":
            cells += [
                self._params(
                    scenario,
                    mode,
                    domain,
                    "100_000",
                    ("input_file", "None,  # a CSV or Parquet file to learn from instead"),
                    ("output_formats", "['terminal']"),
                ),
                self._code(
                    "result = demo_run(params)\n"
                    "print(f\"Session ID: {result['session_id']}\")\n"
                    "if result['fidelity_score'] is not None:\n"
                    "    print(f\"Fidelity: {result['fidelity_score']:.1%}\")"
                ),
                self._code("print(demo_report(result['session_id'])['content'])"),
            ]
        elif mode == "streaming":
            cells += [
                self._params(scenario, mode, domain, "10_000"),
                self._code("result = demo_run(params)"),
            ]
        else:
            cells += [
                self._params(
                    scenario,
                    mode,
                    domain,
                    f"{scenario.default_rows:_}",
                    ("connection", "None,  # the name of your connection profile"),
                ),
                self._code(
                    "result = demo_run(params)\nprint(f\"Session ID: {result['session_id']}\")"
                ),
            ]
        cells += [
            self._markdown("## Cleanup\n\nRun the cell below to remove all demo artifacts."),
            self._code(
                "# from shape.demo import demo_cleanup\n"
                "# demo_cleanup(result['session_id'])  # uncomment to clean up"
            ),
        ]
        return cells
