"""Item 2 without the OpenLineage library: the plan, the destinations and the exit codes."""

from __future__ import annotations

import json
import uuid

import pytest
from shape_integrations import lineage
from shape_integrations.manifest_view import ManifestError, load
from shape_integrations.testing import REPRO, write_manifest

MISSING = (
    "OpenLineage needs the 'openlineage' extra: "
    "pip install 'sqllocks-shape-integrations[openlineage]'"
)


def run(*argv: str) -> int:
    from shape.plugins.cli import run_command
    from shape.plugins.host import default_host

    return run_command(default_host(), "lineage", list(argv))


def test_plan_has_start_then_complete(manifest):
    p = lineage.plan(load(manifest))
    assert [s for s, _ in p.states] == ["START", "COMPLETE"]
    assert p.states[0][1] == "2026-10-03T12:00:00+00:00"
    assert p.states[1][1] == "2026-10-03T12:00:05+00:00"


def test_a_failed_gate_makes_the_last_event_fail(tmp_path):
    p = lineage.plan(load(write_manifest(tmp_path, failed=True)))
    assert [s for s, _ in p.states] == ["START", "FAIL"]


def test_the_run_id_is_a_deterministic_uuid_of_the_shape_run_id(manifest, tmp_path):
    a = lineage.plan(load(manifest))
    b = lineage.plan(load(manifest))
    other = lineage.plan(
        load(write_manifest(tmp_path / "x", run_id="20261003_130000_retail_small_s43"))
    )
    assert a.run_uuid == b.run_uuid
    assert str(uuid.UUID(a.run_uuid)) == a.run_uuid
    assert a.run_uuid != other.run_uuid
    assert a.facet["runId"] == "20261003_120000_retail_small_s42"


def test_one_output_dataset_per_table_with_columns_from_the_output_files(manifest):
    p = lineage.plan(load(manifest))
    assert [d.name for d in p.outputs] == ["customer", "orders"]
    assert dict(p.outputs[0].columns) == {"id": "int64", "email": "string"}
    assert dict(p.outputs[1].columns) == {"id": "int64", "customer_id": "int64", "total": "double"}


def test_a_table_whose_files_are_gone_has_no_schema_not_an_invented_one(tmp_path):
    p = lineage.plan(load(write_manifest(tmp_path, with_files=False)))
    assert [d.name for d in p.outputs] == ["customer", "orders"]
    assert all(d.columns == () for d in p.outputs)


def test_the_shape_facet_holds_the_tuple_and_dataset_id_when_present(manifest):
    f = lineage.plan(load(manifest)).facet
    assert f["reproducibility"] == REPRO
    assert f["datasetId"].startswith("sha256:")
    assert f["version"] == 1


def test_the_shape_facet_omits_them_for_a_manifest_without(tmp_path):
    f = lineage.plan(load(write_manifest(tmp_path, with_repro=False))).facet
    assert "reproducibility" not in f and "datasetId" not in f
    assert f["runId"]


def test_a_manifest_written_before_format_and_version_existed_loads(tmp_path):
    path = write_manifest(tmp_path)
    raw = json.loads(path.read_text())
    for key in ("format", "version", "reproducibility", "dataset_id"):
        raw.pop(key)
    path.write_text(json.dumps(raw))
    f = lineage.plan(load(path)).facet
    assert set(f) == {"version", "runId", "engineVersion"}


def test_missing_times_fall_back_without_inventing_an_order():
    from shape_integrations.manifest_view import ManifestView

    base = dict(path=None, run_id="r", tables=(), failed=False, failed_gates=())
    only_start = lineage.plan(
        ManifestView(**base, started="2026-01-01T00:00:00+00:00", finished="")
    )
    assert [t for _, t in only_start.states] == ["2026-01-01T00:00:00+00:00"] * 2
    neither = lineage.plan(
        ManifestView(**base, started="", finished=""), now="2026-02-02T00:00:00+00:00"
    )
    assert [t for _, t in neither.states] == ["2026-02-02T00:00:00+00:00"] * 2


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param("", id="missing"),
        pytest.param("{", id="not-json"),
        pytest.param('{"format": "something-else", "run_id": "x"}', id="wrong-format"),
        pytest.param('{"format": "shape-run-manifest", "version": 99, "run_id": "x"}', id="newer"),
        pytest.param('["a"]', id="list"),
        pytest.param("{}", id="no-run-id"),
    ],
)
def test_unusable_manifests_are_reported(tmp_path, bad):
    path = tmp_path / "m.json"
    if bad:
        path.write_text(bad)
    with pytest.raises(ManifestError):
        load(path)


@pytest.mark.parametrize(
    ("to", "kind", "target"),
    [
        ("file:///tmp/events.ndjson", "file", "/tmp/events.ndjson"),
        ("file://localhost/tmp/e.ndjson", "file", "/tmp/e.ndjson"),
        ("file:///tmp/with%20space.ndjson", "file", "/tmp/with space.ndjson"),
        ("http://127.0.0.1:5000/api/v1/lineage", "http", "http://127.0.0.1:5000/api/v1/lineage"),
        ("HTTPS://example.org/x", "http", "HTTPS://example.org/x"),
    ],
)
def test_destinations_that_are_accepted(to, kind, target):
    d = lineage.parse_destination(to)
    assert (d.kind, d.target) == (kind, target)


@pytest.mark.parametrize(
    "to",
    [
        "",
        "  ",
        "ftp://x/y",
        "events.ndjson",
        "file://",
        "file:///tmp/",
        "file://otherhost/x",
        "http://",
    ],
)
def test_destinations_that_are_refused(to):
    with pytest.raises(lineage.DestinationError):
        lineage.parse_destination(to)


def test_a_password_in_the_url_is_not_echoed():
    with pytest.raises(lineage.DestinationError) as info:
        lineage.parse_destination("ftp://user:hunter2@host/x")
    assert "hunter2" not in str(info.value)


def test_the_token_is_not_a_command_line_option(manifest, capsys):
    assert run("emit", str(manifest), "--to", "http://127.0.0.1:1/x", "--token", "abc") == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_without_the_library_the_command_exits_2_with_the_pip_command(
    manifest, tmp_path, capsys, hide_library
):
    hide_library("openlineage")
    out = tmp_path / "e.ndjson"
    assert run("emit", str(manifest), "--to", f"file://{out}") == 2
    assert capsys.readouterr().err.strip() == f"shape: error: {MISSING}"
    assert not out.exists()


def test_a_bad_manifest_exits_2(tmp_path, capsys):
    assert run("emit", str(tmp_path / "nope.json"), "--to", f"file://{tmp_path}/e") == 2
    assert "manifest not found" in capsys.readouterr().err


def test_a_bad_destination_exits_2(manifest, capsys):
    assert run("emit", str(manifest), "--to", "ftp://x/y") == 2
    assert "unsupported destination" in capsys.readouterr().err


def test_an_unset_token_variable_exits_2(manifest, monkeypatch, capsys):
    monkeypatch.delenv("SHAPE_TEST_TOKEN", raising=False)
    assert (
        run(
            "emit", str(manifest), "--to", "http://127.0.0.1:1/x", "--token-env", "SHAPE_TEST_TOKEN"
        )
        == 2
    )
    assert "SHAPE_TEST_TOKEN" in capsys.readouterr().err


def test_a_token_is_not_sent_over_plain_http_to_a_remote_host(manifest, monkeypatch, capsys):
    monkeypatch.setenv("SHAPE_TEST_TOKEN", "s3cret-token-value")
    code = run(
        "emit",
        str(manifest),
        "--to",
        "http://lineage.example.org/api",
        "--token-env",
        "SHAPE_TEST_TOKEN",
    )
    err = capsys.readouterr().err
    assert code == 2
    assert "https" in err and "s3cret-token-value" not in err


def test_a_token_with_a_file_destination_is_refused(manifest, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SHAPE_TEST_TOKEN", "s3cret-token-value")
    assert (
        run(
            "emit", str(manifest), "--to", f"file://{tmp_path}/e", "--token-env", "SHAPE_TEST_TOKEN"
        )
        == 2
    )
    assert "s3cret-token-value" not in capsys.readouterr().err


def test_an_empty_namespace_exits_2(manifest, tmp_path, capsys):
    assert run("emit", str(manifest), "--to", f"file://{tmp_path}/e", "--namespace", " ") == 2
