"""The intentional differences between Shape's Fabric commands and the baseline's (P6-07c).

Every entry has a name, the reason, and where the harness shows it (a probe that runs in
``verify.py``: an observation of the baseline, so an entry that stops being true fails the run).
Nothing outside this list may differ: a difference that is not here is a failure.

``BRAND`` is not a defect: Shape names itself where the baseline names itself (D-13), so the
baseline's text is mapped to Shape's before it is compared.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parents[1]))
import _refpkg  # noqa: E402


@dataclass(frozen=True)
class Difference:
    name: str
    command: str
    reason: str
    probe: str  # where verify.py shows it


# (pattern, replacement) applied to the baseline's text before comparing it with Shape's.
BRAND: tuple[tuple[str, str], ...] = (
    (re.escape(_refpkg.ENGINE_CLASS) + r" v\d+\.\d+\.\d+", "Shape vVERSION"),
    (re.escape(_refpkg.ENGINE_CLASS) + "_", "Shape_"),
    (re.escape(_refpkg.NAME) + "-", "shape-"),
    (re.escape(_refpkg.ENGINE_CLASS) + "(?=[A-Z])", "Shape"),  # model name prefix: <Class>Retail
    (re.escape(_refpkg.DIST), "sqllocks-shape"),
    (re.escape(f"'{_refpkg.NAME} notebook'"), "'shape notebook'"),
)


def brand(text: str) -> str:
    """The baseline's ``text`` with its own name mapped to Shape's."""
    for pattern, replacement in BRAND:
        text = re.sub(pattern, replacement, text)
    return text


def qualify_colliding_measures(model: dict[str, Any]) -> None:
    """The ``colliding-measure-names`` entry, applied in place to the baseline's TOM model.

    A measure whose name another measure of the model shares (names compared case-blind, as
    Tabular compares them) becomes ``NAME (TABLE)``; every other name is left exactly as it is.
    """
    names = [m["name"].casefold() for t in model["tables"] for m in t.get("measures", [])]
    shared = {n for n in names if names.count(n) > 1}
    for table in model["tables"]:
        for measure in table.get("measures", []):
            if measure["name"].casefold() in shared:
                measure["name"] = f"{measure['name']} ({table['name']})"


def unversion(text: str) -> str:
    """``text`` with any ``Shape vX.Y.Z`` made ``Shape vVERSION``."""
    text = re.sub(r"Shape v\d+\.\d+\.\d+\S*", "Shape vVERSION", text)
    return re.sub(r"(sqllocks-shape) \d+\.\d+\.\d+\S*", r"\1 VERSION", text)


ALLOWED: tuple[Difference, ...] = (
    Difference(
        "model-name-and-generator",
        "export-model",
        "the model is named for Shape (ShapeRetail, annotation generated_by 'Shape vX'); the "
        "content is otherwise equal, table by table, column by column, measure by measure",
        "export-model rows and the library rows of verify.py",
    ),
    Difference(
        "colliding-measure-names",
        "export-model",
        "the baseline names a measure 'Total <Column>' or 'Avg <Column>' without its table, so two "
        "tables with a column of the same name give two measures one name, and a Tabular model "
        "with a repeated measure name cannot be deployed (retail: product.unit_price and "
        "order_line.unit_price). Shape qualifies a name with its table, 'NAME (TABLE)', only when "
        "another measure of the model would share it; every other measure name is equal "
        "(owner approval 2026-10-03, issue #425)",
        "probe_measure_collisions and qualify_colliding_measures in compare_bim",
    ),
    Difference(
        "m-and-dax-quoting",
        "export-model",
        "a table or column name that holds a double quote, a single quote or a closing bracket "
        "reaches the M text literal, the M identifier and the DAX reference unquoted in the "
        "baseline, so the name ends the literal and the rest is read as expression; Shape quotes "
        'each (\'\' and ]] in DAX, "" and #"..." in M). Names of the shipped domains are plain: '
        "their output is equal",
        "probe_quoting",
    ),
    Difference(
        "notebook-part-path",
        "deploy-notebook",
        "the baseline declares format ipynb but names the part notebook-content.py, which the "
        "Items API does not read as an ipynb part; Shape names it notebook-content.ipynb and adds "
        "the .platform part that carries the display name",
        "probe_notebook_part",
    ),
    Difference(
        "accepted-is-not-created",
        "deploy-notebook, setup-fabric",
        "a 202 (the item is made later) has no body: the baseline reports the notebook created "
        "with 'Item ID: unknown' without checking; Shape follows the operation to its end and "
        "reports the real item, or the failure",
        "probe_accepted",
    ),
    Difference(
        "workspace-listing-pages",
        "deploy-notebook, setup-fabric",
        "the baseline reads the first page of the workspace listing only, so a workspace on a "
        "later page is 'not found'; Shape follows the continuation token",
        "probe_pagination",
    ),
    Difference(
        "setup-existing-items",
        "setup-fabric",
        "a name already in use fails the baseline's command (it reads the 409 as an error); "
        "Shape finds the existing item and says so (the command is safe to run again)",
        "tests/test_fabric_commands.py::test_setup_reuses_what_is_already_there",
    ),
    Difference(
        "manifest-file-paths",
        "publish",
        "the baseline's manifest lists no file for any table (file_paths is empty); Shape lists "
        "the path of each table's file",
        "probe_manifest_paths",
    ),
    Difference(
        "one-landing-layout",
        "publish",
        "the baseline lays a remote lakehouse out one way (Files/landing/DOMAIN/TABLE/latest, "
        "manifest in Files/_control/DOMAIN) and a local folder another (landing/DOMAIN/TABLE/"
        "dt=latest, manifest in landing/DOMAIN/manifest/_control); Shape uses the baseline's "
        "local layout for both",
        "probe_baseline_layouts (reads the baseline's source)",
    ),
    Difference(
        "delta-is-written-or-refused",
        "publish",
        "--format delta to a remote lakehouse writes an empty file in the baseline (the remote "
        "branch serialises parquet, csv and jsonl only); Shape writes Delta tables to a local "
        "folder and refuses OneLake rather than writing nothing",
        "probe_baseline_delta (reads the baseline's source)",
    ),
    Difference(
        "database-write-modes",
        "publish",
        "the baseline's SQL path drops and recreates an existing table; Shape's default fails on "
        "an existing table and --write-mode names the destructive choices (P6-07a)",
        "plugins/shape-fabric/tests/test_publish.py",
    ),
    Difference(
        "exit-codes",
        "all",
        "the baseline exits 1 for every failure; Shape exits 2 for a wrong command line or "
        "input and 1 for a service or destination failure (the convention of every Shape command)",
        "the exit-code rows of verify.py (non-zero in both, 2 in Shape)",
    ),
    Difference(
        "shape-wording",
        "notebook, deploy-notebook, setup-fabric, publish",
        "the generated notebook's text, the next-steps list of setup-fabric (it adds the domains "
        "package Shape's library needs) and the per-table summary table of publish are Shape's "
        "own; structure, counts and every request are compared",
        "the notebook and setup rows of verify.py",
    ),
    Difference(
        "extra-options",
        "export-model, publish, deploy-notebook, setup-fabric",
        "options the baseline lacks: export-model --mode; publish --write-mode, --batch-size, "
        "--schema-name, --staging-path, --workspace-id env names (SHAPE_*), the six --auth modes "
        "and their companions; deploy-notebook and setup-fabric the same --auth modes; "
        "setup-fabric --lakehouse-name. None changes the baseline's behaviour when absent",
        "tests/test_export_model.py, tests/test_publish.py, tests/test_fabric_commands.py",
    ),
)
