"""DEMO-REHEARSAL: what a presenter types and reads, checked against what the demo does.

Each test pins a defect the 2026-10-03 rehearsal found (docs/plans/lane_status/DEMO-REHEARSAL.md):
the progress and plan lines must give the rows the scale preset generates, not the ``--rows``
asked for; every command in ``docs/DEMO.md`` must parse; the notebook must install the wheels a
presenter uploads; a scenario must not describe tables it does not generate.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
from demo_helpers import ROWS, write_schema

ROOT = Path(__file__).resolve().parents[2]
DEMO_MD = ROOT / "docs" / "DEMO.md"
PRESET_ROWS = sum(ROWS.values())  # the ``small`` preset of the test schema: 4,340 rows


@pytest.fixture
def local_profile(run, tmp_path):
    target = tmp_path / "landing"
    assert run("demo", "init", "--name", "local", "--local-path", target)[0] == 0
    return target


# ---- the rows a run says it makes are the rows it makes --------------------------------------


def test_seeding_progress_gives_the_presets_rows_not_the_rows_asked_for(run, schema_file):
    code, out, _ = run(
        "demo", "run", "retail", "--mode", "seeding", "--domain", schema_file, "--rows", "1000"
    )  # fmt: skip
    assert code == 0
    assert f"small scale preset: {PRESET_ROWS:,} rows" in out
    assert "1,000 rows" not in out  # --rows picks a preset; it is not a count


def test_seeding_dry_run_and_estimate_plan_the_presets_rows(run, local_profile, schema_file):
    code, out, _ = run(
        "demo", "run", "retail", "--mode", "seeding", "--connection", "local",
        "--domain", schema_file, "--rows", "1000", "--dry-run",
    )  # fmt: skip
    assert code == 0
    assert f"Rows to generate: {PRESET_ROWS:,}" in out
    assert f"Would write {PRESET_ROWS:,} rows (small scale preset) to: local" in out
    assert "1,000 rows" not in out


def test_a_composite_dry_run_adds_the_rows_of_every_domain(run, tmp_path):
    first = write_schema(tmp_path / "a.json", name="a")
    second = write_schema(tmp_path / "b.json", name="b")
    code, out, _ = run(
        "demo", "run", "enterprise", "--mode", "seeding", "--domains", f"{first},{second}",
        "--rows", "1000", "--estimate",
    )  # fmt: skip
    assert code == 0 and f"Rows to generate: {2 * PRESET_ROWS:,}" in out


def test_inference_progress_gives_the_rows_it_then_generates(run, schema_file):
    code, out, _ = run("demo", "run", "retail", "--domain", schema_file, "--rows", "1000")
    assert code == 0
    generated = re.search(r"Generated ([\d,]+) total rows", out)
    assert generated is not None
    assert f"small scale preset: {generated.group(1)} rows" in out
    assert "1,000 rows" not in out


def test_a_plan_for_a_preset_the_domain_lacks_still_names_the_preset(run, schema_file):
    # The test schema has only ``small``: the plan cannot count the rows, so it names the preset
    # and the rows asked for, and still exits 0 (the run itself fails with its message).
    code, out, _ = run(
        "demo", "run", "retail", "--mode", "seeding", "--domain", schema_file, "--dry-run"
    )
    assert code == 0 and "large scale preset" in out and "(--rows 100,000)" in out


# ---- docs/DEMO.md ------------------------------------------------------------------------------


def documented_demo_commands() -> list[list[str]]:
    text = DEMO_MD.read_text(encoding="utf-8")
    commands = []
    for block in re.findall(r"```bash\n(.*?)```", text, flags=re.DOTALL):
        for line in block.splitlines():
            line = line.split("#", 1)[0].strip()
            if line.startswith("shape demo "):
                commands.append(line.split()[1:])
    return commands


def test_docs_demo_md_lists_the_quickstart_commands():
    names = {cmd[1] for cmd in documented_demo_commands()}
    assert {"list", "run", "init", "status", "report", "cleanup", "notebook"} <= names


@pytest.mark.parametrize("argv", documented_demo_commands(), ids=" ".join)
def test_every_command_in_demo_md_parses(run, monkeypatch, argv):
    import shape.cli.demo as demo_cli

    seen: list[str] = []
    for name in demo_cli._COMMANDS:
        monkeypatch.setitem(demo_cli._COMMANDS, name, lambda a, _n=name: seen.append(_n) or 0)
    code, _, err = run(*argv)
    assert code == 0, err
    assert seen == [argv[1]]


def test_the_quickstart_seeding_run_names_a_small_size():
    # Without --rows the retail scenario generates its 100,000-row preset: 19.6 million rows,
    # over a minute and hundreds of MB on a laptop. The quick start must not do that.
    for argv in documented_demo_commands():
        if argv[1] == "run" and "seeding" in argv:
            assert "--rows" in argv, " ".join(argv)


# ---- the notebook ------------------------------------------------------------------------------


def test_the_notebook_installs_from_the_uploaded_wheels(run, tmp_path):
    out = tmp_path / "retail.ipynb"
    assert run("demo", "notebook", "retail", "--mode", "seeding", "--output", out)[0] == 0
    nb = json.loads(out.read_text(encoding="utf-8"))
    install = "".join(nb["cells"][1]["source"])
    pip = next(ln for ln in install.splitlines() if ln.startswith("%pip install"))
    # by file path, never by name: PyPI's older package of the same version must never win (F-4)
    assert " builtin/sqllocks_shape-" in pip and "==" not in pip and "--find-links" not in pip
    assert "Resources > builtin" in install  # says where the wheels go


# ---- the catalog -------------------------------------------------------------------------------


def test_a_scenario_describes_only_tables_it_generates():
    from shape.demo.catalog import get_catalog
    from shape.demo.modes.common import load_schema

    pytest.importorskip("shape_domains")
    for meta in get_catalog().list():
        tables = set()
        for domain in meta.domains:
            tables |= set(load_schema(domain).tables)
        claimed = set(re.findall(r"\b(?:Dim|Fact)[A-Z]\w+", meta.description))
        assert claimed <= tables, f"{meta.name}: {sorted(claimed - tables)} are not generated"


def test_a_streaming_estimate_counts_the_small_preset_it_streams_from(run, schema_file):
    code, out, _ = run(
        "demo", "run", "retail", "--mode", "streaming", "--domain", schema_file, "--estimate"
    )
    assert code == 0 and f"Rows to generate: {PRESET_ROWS:,}" in out
    assert "100,000" not in out  # the scenario's default --rows does not apply to streaming


def test_the_preset_sizes_in_demo_md_are_the_rows_retail_generates():
    from shape.demo.modes.common import planned_rows

    pytest.importorskip("shape_domains")
    text = DEMO_MD.read_text(encoding="utf-8")
    rows = dict(re.findall(r"\| `(small|medium|large)` \| ([\d,]+) \|", text))
    assert set(rows) == {"small", "medium", "large"}
    for scale, documented in rows.items():
        assert planned_rows(["retail"], scale) == int(documented.replace(",", "")), scale


def test_an_inference_plan_counts_the_rows_of_the_schema_it_learns(run, schema_file):
    # Inference generates from the schema it learns, whose presets are not the domain's own:
    # the plan must give the rows the run then generates, not the domain schema's.
    code, planned, _ = run("demo", "run", "retail", "--domain", schema_file, "--dry-run")
    assert code == 0
    code, ran, _ = run("demo", "run", "retail", "--domain", schema_file)
    assert code == 0
    generated = re.search(r"Generated ([\d,]+) total rows", ran)
    assert generated is not None
    assert f"Rows to generate: {generated.group(1)}\n" in planned
    assert f"scale preset: {generated.group(1)} rows" in planned


def test_the_inference_sizes_in_demo_md_are_what_its_dry_run_counts(run):
    pytest.importorskip("shape_domains")
    text = " ".join(DEMO_MD.read_text(encoding="utf-8").split())
    found = re.search(r"for `retail`, ([\d,]+) rows at `small` and ([\d,]+) at `large`", text)
    assert found is not None
    for rows, documented in (("1000", found.group(1)), ("100000", found.group(2))):
        code, out, _ = run("demo", "run", "retail", "--rows", rows, "--dry-run")
        assert code == 0 and f"Rows to generate: {documented}\n" in out


# ---- the comparison page -----------------------------------------------------------------------


def test_the_charts_page_matches_integer_values_written_as_floats():
    from types import SimpleNamespace

    from shape.demo.charts import _shares

    synthetic = SimpleNamespace(dtype="integer", enum_values={"3.0": 0.43, "1.0": 0.14})
    assert _shares(synthetic) == {"3": 0.43, "1": 0.14}
    text = SimpleNamespace(dtype="string", enum_values={"3.0": 0.5})
    assert _shares(text) == {"3.0": 0.5}  # only integer columns are normalized


def test_the_charts_page_orders_tied_values_the_same_way_every_time():
    from shape.demo.charts import _categories

    real = {"2": 0.42, "3": 0.42, "1": 0.16}
    synthetic = {"3": 0.43, "2": 0.43, "1": 0.14}
    expected = _categories(real, synthetic)
    assert _categories(dict(reversed(real.items())), dict(reversed(synthetic.items()))) == expected
    assert expected[-1] == "1"


def test_the_charts_page_is_the_same_bytes_whatever_the_hash_seed(tmp_path):
    import os
    import subprocess

    pytest.importorskip("shape_domains")
    pages = []
    for seed in ("1", "2"):
        out = tmp_path / seed
        env = dict(os.environ, PYTHONHASHSEED=seed, SHAPE_HOME=str(tmp_path / "home"))
        argv = [sys.executable, "-m", "shape", "demo", "run", "retail", "--rows", "1000"]
        argv += ["--output", "charts", "--output-dir", str(out)]
        subprocess.run(argv, env=env, check=True, capture_output=True)
        pages.append((out / "retail_charts.html").read_bytes())
    assert pages[0] == pages[1]
