"""ISS-verify (issue #7): inline ``%pip`` is disabled by default in Fabric pipeline runs.

Microsoft Learn ("Manage Apache Spark libraries", Python inline installation): inline commands are
disabled in notebook pipeline runs unless the notebook activity passes a Boolean parameter
``_inlineInstallationEnabled`` set to true; Python notebooks cannot attach an Environment
("Environment integration isn't available on Python notebooks"). So every notebook a pipeline
runs that installs with ``%pip`` must be passed the flag, and no other activity may claim it.
Whether Fabric honours it is checked in the workspace (RUNBOOK section 10, ``[VERIFY]``).
"""

from __future__ import annotations

import json

import nbformat
import pytest
from fabric_helpers import NOTEBOOKS, PIPELINES

FLAG = "_inlineInstallationEnabled"


def _notebook_activities():
    for pdir in sorted(PIPELINES.glob("*.DataPipeline")):
        content = json.loads((pdir / "pipeline-content.json").read_text())

        def walk(acts):
            for a in acts:
                yield a
                tp = a.get("typeProperties", {})
                yield from walk(tp.get("ifTrueActivities", []))
                yield from walk(tp.get("ifFalseActivities", []))

        for a in walk(content["properties"]["activities"]):
            if a["type"] == "TridentNotebook":
                name = a["typeProperties"]["notebookId"].split(":")[1].rstrip(">")
                yield pdir.name, a["name"], name, a["typeProperties"]["parameters"]


def _installs_inline(notebook: str) -> bool:
    nb = nbformat.read(NOTEBOOKS / f"{notebook}.ipynb", as_version=4)
    return any(
        line.lstrip().startswith(("%pip", "!pip", "%conda"))
        for c in nb.cells
        if c.cell_type == "code"
        for line in c.source.splitlines()
    )


CASES = list(_notebook_activities())


def test_the_pipelines_run_notebooks():
    assert {c[2] for c in CASES} >= {"shape_profile", "shape_profile_spark", "shape_generate"}


@pytest.mark.parametrize("pipeline, activity, notebook, params", CASES)
def test_pip_installing_notebooks_get_the_inline_install_flag(pipeline, activity, notebook, params):
    if _installs_inline(notebook):
        assert params.get(FLAG) == {"value": True, "type": "bool"}, (pipeline, activity)
    else:
        assert FLAG not in params, (pipeline, activity)
