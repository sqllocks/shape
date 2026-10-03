"""P6-12: ``demo notebook``, and the files an inference run writes (charts, semantic model)."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from shape import __version__

ROOT = Path(__file__).resolve().parents[2]


def session_of(out: str) -> str:
    return next(x for x in out.splitlines() if x.startswith("Session: ")).split(": ", 1)[1].strip()


def notebook(run, tmp_path, scenario="retail", mode="inference"):
    path = tmp_path / f"{scenario}-{mode}.ipynb"
    code, out, err = run("demo", "notebook", scenario, "--mode", mode, "--output", path)
    assert code == 0, err
    assert f"Notebook written to: {path}" in out
    return json.loads(path.read_text()), path


@pytest.mark.parametrize(
    ("scenario", "mode"),
    [("retail", "inference"), ("retail", "streaming"), ("retail", "seeding"),
     ("adventureworks", "seeding"), ("enterprise", "seeding")],
)  # fmt: skip
def test_a_notebook_is_a_valid_nbformat_document_that_runs_the_scenario(
    run, tmp_path, scenario, mode
):
    nb, _ = notebook(run, tmp_path, scenario, mode)
    assert nb["nbformat"] == 4 and nb["nbformat_minor"] == 5
    ids = [c["id"] for c in nb["cells"]]
    assert len(ids) == len(set(ids)) and all(ids)  # nbformat 4.5 needs a unique id per cell
    for cell in nb["cells"]:
        assert cell["cell_type"] in ("code", "markdown")
        assert isinstance(cell["source"], list) and all(isinstance(x, str) for x in cell["source"])
        if cell["cell_type"] == "code":
            assert cell["outputs"] == [] and cell["execution_count"] is None
            compile("".join(cell["source"]).replace("%pip", "#%pip"), "<cell>", "exec")
    code = "\n".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code")
    assert f"scenario={scenario!r}" in code and f"mode='{mode}'" in code
    assert "from shape.demo import demo_report, demo_run" in code
    assert "SEED = 42" in code
    assert "spindle" not in json.dumps(nb).lower()


def test_the_install_cell_installs_the_packages_the_other_notebooks_install(run, tmp_path):
    nb, _ = notebook(run, tmp_path)
    install = "".join(nb["cells"][1]["source"])
    spec = importlib.util.spec_from_file_location(
        "build_notebooks", ROOT / "integrations" / "fabric" / "notebooks" / "build_notebooks.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    shared = re.search(r"%pip install .*", module.INSTALL_CELL[1])
    assert shared is not None
    mine = set(re.findall(r'"(sqllocks-[a-z-]+)==', install))
    theirs = set(re.findall(r'"(sqllocks-[a-z-]+)==', shared.group(0)))
    assert theirs <= mine  # the same core and domain packages, plus the Fabric plugin
    assert mine - theirs == {"sqllocks-shape-fabric"}
    assert f"=={__version__}" in install


def test_the_same_notebook_is_the_same_bytes(run, tmp_path):
    _, first = notebook(run, tmp_path / "a")
    _, second = notebook(run, tmp_path / "b")
    assert first.read_bytes() == second.read_bytes()


def test_a_composite_scenarios_notebook_does_not_pin_one_domain(run, tmp_path):
    nb, _ = notebook(run, tmp_path, "enterprise", "seeding")
    code = "\n".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code")
    assert "domain=" not in code  # the scenario's own domains are used


def test_a_notebook_for_a_mode_the_scenario_lacks_is_refused(run, tmp_path):
    code, _, err = run(
        "demo", "notebook", "enterprise", "--mode", "streaming", "--output", tmp_path / "x.ipynb"
    )
    assert code == 2 and "does not support mode 'streaming'" in err
    assert not (tmp_path / "x.ipynb").exists()
    code, _, err = run("demo", "notebook", "nope")
    assert code == 2 and "scenario 'nope' not found" in err


def test_the_default_notebook_name_is_in_the_working_folder(run, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert run("demo", "notebook", "retail", "--mode", "seeding")[0] == 0
    assert (tmp_path / "shape_retail_seeding.ipynb").exists()


# ---- charts and the semantic model -------------------------------------------------------------


def test_charts_are_one_self_contained_page_and_cleanup_removes_only_it(
    run, home, tmp_path, schema_file
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "mine.txt").write_text("keep")
    code, out, _ = run(
        "demo", "run", "retail", "--domain", schema_file, "--rows", "1000", "--seed", "5",
        "--output", "terminal,charts", "--output-dir", out_dir,
    )  # fmt: skip
    assert code == 0, out
    page = out_dir / "retail_charts.html"
    text = page.read_text()
    assert text.startswith("<!DOCTYPE html>") and "Shape demo — retail" in text
    assert "<script" not in text and "http://" not in text and "https://" not in text
    for table in ("customer", "order", "order_line"):
        assert f"<h2>{table}</h2>" in text
    assert re.search(r'class="score">\d+\.\d%</p>', text)

    sid = session_of(out)
    code, out, _ = run("demo", "cleanup", sid)
    assert code == 0 and "Removed: file/retail_charts.html" in out
    assert not page.exists() and (out_dir / "mine.txt").read_text() == "keep"


def test_the_semantic_model_is_a_bim_of_the_learned_schema(run, home, tmp_path, schema_file):
    out_dir = tmp_path / "out"
    code, out, err = run(
        "demo", "run", "retail", "--domain", schema_file, "--rows", "1000", "--seed", "5",
        "--output", "semantic_model", "--output-dir", out_dir,
    )  # fmt: skip
    assert code == 0, err
    bim = json.loads((out_dir / "retail_model.bim").read_text())
    assert {t["name"] for t in bim["model"]["tables"]} == {"customer", "order", "order_line"}
    assert any(a["name"] == "generated_by" for a in bim["model"]["annotations"])
    assert "spindle" not in json.dumps(bim).lower()


def test_all_writes_the_page_and_the_model(run, tmp_path, schema_file):
    out_dir = tmp_path / "out"
    code, _, _ = run(
        "demo", "run", "retail", "--domain", schema_file, "--rows", "1000", "--output", "all",
        "--output-dir", out_dir,
    )  # fmt: skip
    assert code == 0
    assert sorted(p.name for p in out_dir.iterdir()) == ["retail_charts.html", "retail_model.bim"]


def test_the_semantic_model_by_name_needs_the_plugin_all_just_skips_it(
    tmp_path, monkeypatch, schema_file
):
    import sys

    from shape.demo.api import demo_run

    monkeypatch.setitem(sys.modules, "shape_fabric.semantic_model", None)  # not importable
    base = {
        "scenario": "retail",
        "domain": str(schema_file),
        "rows": 1000,
        "output_dir": str(tmp_path),
    }
    named = demo_run({**base, "output_formats": ["semantic_model"]})
    assert named["success"] is False and "needs the shape-fabric plugin" in named["error"]
    skipped = demo_run({**base, "output_formats": ["all"]})
    assert skipped["success"] is True
    assert sorted(p.name for p in tmp_path.glob("retail_*")) == ["retail_charts.html"]
