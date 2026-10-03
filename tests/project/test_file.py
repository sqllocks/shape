"""W1-04 deliverable 1 and 4: the ``shape.yml`` format, its versioning, and validation errors."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from shape.project import (
    FORMAT,
    VERSION,
    ProjectError,
    ProjectVersionError,
    find_project,
    load_project,
    parse_project,
    problems,
)

GOOD = """\
format: shape-project
version: 1
name: orders-platform
sources:
  orders:
    path: data/orders
    dataset: true
    contract: contracts/orders.json
    baseline:
      kind: rolling_window
      window: 7
      registry: shapes/registry
      name: orders-daily
    thresholds:
      null_rate: 0.02
    ignore: [load_ts]
    columns:
      amount:
        thresholds:
          mean_shift_std: 1.0
        owner: finance@example.com
        annotations:
          unit: EUR
      batch_id:
        ignore: true
gates:
  schema_conformance:
    mode: enforce
  distribution:
    mode: observe
"""


def doc(**over: object) -> dict:
    base: dict = {
        "format": FORMAT,
        "version": VERSION,
        "sources": {"orders": {"path": "data/orders"}},
    }
    base.update(over)
    return base


def test_constants():
    assert FORMAT == "shape-project"
    assert VERSION == 1 and isinstance(VERSION, int)


def test_good_document_loads_every_field(tmp_path: Path):
    f = tmp_path / "shape.yml"
    f.write_text(GOOD, encoding="utf-8")
    p = load_project(f)
    assert p.name == "orders-platform"
    assert p.root == tmp_path
    src = p.source("orders")
    assert src.path == str(tmp_path / "data" / "orders")  # relative to the file's folder
    assert src.dataset is True
    assert src.contract == str(tmp_path / "contracts" / "orders.json")
    assert src.baseline is not None
    assert (src.baseline.kind, src.baseline.window, src.baseline.name) == (
        "rolling_window",
        7,
        "orders-daily",
    )
    assert src.baseline.registry == str(tmp_path / "shapes" / "registry")
    assert src.owner_of("amount") == "finance@example.com"
    assert src.annotations_of("amount") == {"unit": "EUR"}
    assert src.owner_of("id") is None
    assert p.gates == {"schema_conformance": "enforce", "distribution": "observe"}
    assert p.gate_mode("null_constraint") == "enforce"  # not listed: unchanged behaviour
    assert p.gate_mode("distribution") == "observe"


def test_drift_policy_is_built_from_thresholds_columns_and_ignore(tmp_path: Path):
    p = parse_project(GOOD, tmp_path / "shape.yml")
    assert p.source("orders").drift_policy() == {
        "thresholds": {"null_rate": 0.02},
        "columns": {"amount": {"mean_shift_std": 1.0}},
        "ignore": ["load_ts", "batch_id"],
    }


def test_uris_are_not_made_relative(tmp_path: Path):
    text = GOOD.replace("path: data/orders", "path: abfss://lake@acct.dfs.core.windows.net/orders")
    p = parse_project(text, tmp_path / "shape.yml")
    assert p.source("orders").path == "abfss://lake@acct.dfs.core.windows.net/orders"


def test_unknown_source_is_an_error_naming_the_known_ones(tmp_path: Path):
    p = parse_project(GOOD, tmp_path / "shape.yml")
    with pytest.raises(ProjectError, match=r"no source 'nope'.*orders"):
        p.source("nope")


def test_minimal_document_is_valid():
    assert problems(doc()) == []


@pytest.mark.parametrize(
    ("document", "needle"),
    [
        ({"version": 1, "sources": {}}, "missing required key 'format'"),
        (doc(format="shape-model"), "expected 'shape-project'"),
        (doc(version="1"), "version"),
        (doc(version=True), "version"),
        (doc(version=1.0), "version"),
        (doc(version=0), "version"),
        ({"format": FORMAT, "version": 1}, "missing required key 'sources'"),
        (doc(extra=1), "unexpected key 'extra'"),
        (doc(sources=[]), "sources"),
        (doc(sources={"bad name": {"path": "x"}}), "source name 'bad name'"),
        (doc(sources={"../x": {"path": "x"}}), "source name '../x'"),
        (doc(sources={"a": {}}), "missing required key 'path'"),
        (doc(sources={"a": {"path": ""}}), "sources.a.path: must not be empty"),
        (doc(sources={"a": {"path": "x", "dataset": "yes"}}), "sources.a.dataset"),
        (doc(sources={"a": {"path": "x", "colour": 1}}), "unexpected key 'colour'"),
        (doc(sources={"a": {"path": "x", "thresholds": {"nope": 1}}}), "unknown threshold 'nope'"),
        (doc(sources={"a": {"path": "x", "thresholds": {"null_rate": -1}}}), "null_rate"),
        (doc(sources={"a": {"path": "x", "thresholds": {"null_rate": True}}}), "null_rate"),
        (doc(sources={"a": {"path": "x", "thresholds": {"min_severity": "huge"}}}), "min_severity"),
        (doc(sources={"a": {"path": "x", "ignore": "id"}}), "sources.a.ignore"),
        (doc(sources={"a": {"path": "x", "ignore": [1]}}), "sources.a.ignore[0]"),
        (doc(sources={"a": {"path": "x", "columns": {"c": {"owner": ""}}}}), "owner"),
        (doc(sources={"a": {"path": "x", "columns": {"c": {"owner": 3}}}}), "owner"),
        (doc(sources={"a": {"path": "x", "columns": {"c": {"annotations": {"k": [1]}}}}}), "k"),
        (doc(sources={"a": {"path": "x", "columns": {"c": {"colour": 1}}}}), "unexpected key"),
        (doc(sources={"a": {"path": "x", "columns": {"c": {"thresholds": {"x": 1}}}}}), "'x'"),
        (doc(gates={"nope": {"mode": "enforce"}}), "unknown gate 'nope'"),
        (doc(gates={"distribution": {"mode": "warn"}}), "mode"),
        (doc(gates={"distribution": {}}), "missing required key 'mode'"),
        (doc(gates={"distribution": "observe"}), "gates.distribution"),
    ],
)
def test_invalid_documents_name_the_key_path(document: dict, needle: str):
    found = problems(document)
    assert found, "expected at least one problem"
    assert any(needle in line for line in found), found


def test_every_problem_is_reported_at_once():
    found = problems(
        doc(
            sources={"a": {"path": "", "thresholds": {"nope": 1}}},
            gates={"zzz": {"mode": "observe"}},
        )
    )
    assert len(found) >= 3


@pytest.mark.parametrize(
    ("baseline", "needle"),
    [
        ({}, "missing required key 'kind'"),
        ({"kind": "yesterday"}, "kind"),
        ({"kind": "rolling_window"}, "window"),
        ({"kind": "rolling_window", "window": 0}, "window"),
        ({"kind": "rolling_window", "window": 2.5}, "window"),
        ({"kind": "rolling_window", "window": True}, "window"),
        ({"kind": "previous_run", "window": 3}, "window only applies to rolling_window"),
        ({"kind": "pinned"}, "pinned needs exactly one of artifact or ref"),
        ({"kind": "pinned", "artifact": "a.shape", "ref": "v1"}, "exactly one of artifact or ref"),
        ({"kind": "previous_run", "artifact": "a.shape"}, "only applies to pinned"),
        ({"kind": "same_weekday", "ref": "v1"}, "only applies to pinned"),
        ({"kind": "month_end", "registry": ""}, "registry"),
        ({"kind": "month_end", "name": "bad name"}, "name"),
        ({"kind": "month_end", "nope": 1}, "unexpected key 'nope'"),
    ],
)
def test_baseline_rules(baseline: dict, needle: str):
    found = problems(doc(sources={"a": {"path": "x", "baseline": baseline}}))
    assert any(needle in line for line in found), found


@pytest.mark.parametrize(
    "baseline",
    [
        {"kind": "previous_run"},
        {"kind": "same_weekday"},
        {"kind": "rolling_window", "window": 1},
        {"kind": "month_end"},
        {"kind": "pinned", "artifact": "baselines/orders.shape"},
        {"kind": "pinned", "ref": "production"},
    ],
)
def test_every_baseline_kind_is_valid(baseline: dict):
    assert problems(doc(sources={"a": {"path": "x", "baseline": baseline}})) == []


def test_baseline_defaults(tmp_path: Path):
    text = GOOD.replace(
        "      kind: rolling_window\n      window: 7\n      registry: shapes/registry\n"
        "      name: orders-daily\n",
        "      kind: previous_run\n",
    )
    b = parse_project(text, tmp_path / "shape.yml").source("orders").baseline
    assert b is not None
    assert b.registry == str(tmp_path / "shapes" / "registry")
    assert b.name == "orders"  # the source's name


# ---- versioning ----------------------------------------------------------------------------


def test_a_newer_version_has_its_own_error(tmp_path: Path):
    text = GOOD.replace("version: 1\n", "version: 2\n", 1)
    with pytest.raises(ProjectVersionError, match=r"version 2 is newer.*up to version 1"):
        parse_project(text, tmp_path / "shape.yml")
    assert issubclass(ProjectVersionError, ProjectError)


def test_newer_version_is_reported_before_unknown_keys(tmp_path: Path):
    text = "format: shape-project\nversion: 2\nsources: {}\nfuture_key: 1\n"
    with pytest.raises(ProjectVersionError):
        parse_project(text, tmp_path / "shape.yml")


# ---- parsing -------------------------------------------------------------------------------


def test_yaml_syntax_error_names_file_line_and_column(tmp_path: Path):
    f = tmp_path / "shape.yml"
    f.write_text("format: shape-project\nversion: 1\nsources: [\n  oops\n", encoding="utf-8")
    with pytest.raises(ProjectError) as e:
        load_project(f)
    msg = str(e.value)
    assert str(f) in msg and "line" in msg


def test_duplicate_keys_are_refused(tmp_path: Path):
    text = GOOD.replace("name: orders-platform\n", "name: a\nname: b\n")
    with pytest.raises(ProjectError, match=r"duplicate key 'name'.*line 4"):
        parse_project(text, tmp_path / "shape.yml")


def test_document_must_be_a_mapping(tmp_path: Path):
    with pytest.raises(ProjectError, match="must be a mapping"):
        parse_project("- a\n- b\n", tmp_path / "shape.yml")
    with pytest.raises(ProjectError, match="is empty"):
        parse_project("", tmp_path / "shape.yml")


def test_problems_are_raised_together_with_the_file_name(tmp_path: Path):
    f = tmp_path / "shape.yml"
    f.write_text(
        "format: shape-project\nversion: 1\nsources:\n  a:\n    path: ''\n    nope: 1\n",
        encoding="utf-8",
    )
    with pytest.raises(ProjectError) as e:
        load_project(f)
    assert str(f) in str(e.value)
    assert "sources.a.path" in str(e.value) and "nope" in str(e.value)
    assert len(e.value.problems) == 2


def test_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_project(tmp_path / "shape.yml")


def test_not_utf8(tmp_path: Path):
    f = tmp_path / "shape.yml"
    f.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(ProjectError, match="not a UTF-8 text file"):
        load_project(f)


def test_without_pyyaml_the_error_names_the_extra(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(sys.modules, "yaml", None)
    with pytest.raises(ProjectError, match=r"sqllocks-shape\[yaml\]"):
        parse_project(GOOD, tmp_path / "shape.yml")


def test_yaml_is_not_a_core_dependency():
    meta = (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    core = meta.split("dependencies=[", 1)[1].split("]", 1)[0]
    assert "yaml" not in core.lower()
    assert 'yaml=["pyyaml' in meta  # the existing optional extra


# ---- discovery -----------------------------------------------------------------------------


def test_find_project_walks_up_and_stops_at_the_repository_root(tmp_path: Path):
    outer = tmp_path / "outer"
    repo = outer / "repo"
    deep = repo / "a" / "b"
    deep.mkdir(parents=True)
    (outer / "shape.yml").write_text("x", encoding="utf-8")
    (repo / ".git").mkdir()
    assert find_project(deep) is None  # outer/shape.yml is above the repository root
    (repo / "shape.yml").write_text("x", encoding="utf-8")
    assert find_project(deep) == repo / "shape.yml"
    assert find_project(repo) == repo / "shape.yml"


def test_find_project_accepts_yaml_suffix_and_prefers_yml(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "shape.yaml").write_text("x", encoding="utf-8")
    assert find_project(tmp_path) == tmp_path / "shape.yaml"
    (tmp_path / "shape.yml").write_text("x", encoding="utf-8")
    assert find_project(tmp_path) == tmp_path / "shape.yml"


def test_json_is_accepted_because_it_is_yaml(tmp_path: Path):
    text = json.dumps(doc())
    assert parse_project(text, tmp_path / "shape.yml").source("orders").path.endswith("orders")


@pytest.mark.parametrize("name", ["safe", "validate", "export", "import", "list", "registry"])
def test_a_source_cannot_be_named_like_a_profile_subcommand(name: str):
    found = problems(doc(sources={name: {"path": "x"}}))
    assert any("subcommand" in line for line in found), found


def test_oversized_file_is_refused(tmp_path: Path):
    f = tmp_path / "shape.yml"
    f.write_text("#" * (1 << 20) + "\nformat: shape-project\n", encoding="utf-8")
    with pytest.raises(ProjectError, match="larger than 1 MiB"):
        load_project(f)
    with pytest.raises(ProjectError, match="larger than 1 MiB"):
        parse_project(f.read_text(encoding="utf-8"), f)


def test_yaml_tags_cannot_build_objects(tmp_path: Path):
    text = "format: !!python/object/apply:os.system ['echo hi']\nversion: 1\nsources: {}\n"
    with pytest.raises(ProjectError, match="invalid YAML"):
        parse_project(text, tmp_path / "shape.yml")
