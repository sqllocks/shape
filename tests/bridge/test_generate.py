"""P6-11 deliverable 2 (generate, preview): the same data and files as the CLI, and the refusals."""

from __future__ import annotations

import hashlib
import json
import time

import pyarrow.parquet as pq
import pytest

from shape.cli.main import main


def cli(capsys, *argv):
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    assert code == 0, out.err
    return out.out


# ---- generate -------------------------------------------------------------------------------


def test_generate_summary_matches_the_cli_counts(api, capsys):
    result = api.ok("generate", domain="retail", scale="small", seed=7)
    counts = json.loads(
        cli(capsys, "generate", "retail", "--scale", "small", "--seed", 7, "--json")
    )
    assert {t: v["rows"] for t, v in result["tables"].items()} == counts["counts"]
    assert result["total_rows"] == sum(counts["counts"].values())
    assert result["seed"] == 7 and result["scale"] == "small"
    assert result["integrity_pass"] is True and result["integrity_errors"] == []
    assert all(v["columns"] > 0 for v in result["tables"].values())
    assert "files" not in result and "output_dir" not in result


def test_generate_is_deterministic_for_a_seed(api):
    a = api.ok("generate", domain="retail", scale="small", seed=3)
    b = api.ok("generate", domain="retail", scale="small", seed=3)
    assert {k: a[k] for k in ("tables", "total_rows", "seed")} == {
        k: b[k] for k in ("tables", "total_rows", "seed")
    }


@pytest.mark.parametrize(
    "fmt, ext", [("csv", "csv"), ("parquet", "parquet"), ("jsonl", "jsonl"), ("tsv", "tsv")]
)
def test_generate_writes_the_files_the_cli_writes(api, capsys, tmp_path, fmt, ext):
    out = tmp_path / "bridge"
    result = api.ok(
        "generate", domain="retail", scale="small", seed=5, format=fmt, output_dir=str(out)
    )
    ref = tmp_path / "cli"
    cli(capsys, "generate", "retail", "--scale", "small", "--seed", 5, "--format", fmt, "-o", ref)
    assert result["output_format"] == fmt and result["output_dir"] == str(out)
    assert sorted(p.split("/")[-1] for p in result["files"]) == sorted(
        p.name for p in ref.iterdir() if p.name != "_shape_provenance.json"
    )
    assert all(p.endswith(f".{ext}") for p in result["files"])
    for path in result["files"]:
        name = path.split("/")[-1]
        assert (out / name).read_bytes() == (ref / name).read_bytes() or fmt == "parquet"
    if fmt == "parquet":
        for name, info in result["tables"].items():
            mine, theirs = (
                pq.read_table(out / f"{name}.parquet"),
                pq.read_table(ref / f"{name}.parquet"),
            )
            assert mine.num_rows == info["rows"] and mine.equals(theirs)


def test_generate_a_schema_file(api, schema_file, tmp_path):
    result = api.ok(
        "generate", domain=str(schema_file), format="csv", output_dir=str(tmp_path / "o")
    )
    assert {t: v["rows"] for t, v in result["tables"].items()} == {
        "customer": 40,
        "order": 1200,
        "order_line": 3100,
    }
    assert (tmp_path / "o" / "order_line.csv").is_file()


def test_generate_a_format_without_a_directory_is_refused(api):
    e = api.fail("generate", "usage.missing_argument", domain="retail", scale="small", format="csv")
    assert "output_dir" in e["message"]


def test_summary_with_a_directory_writes_nothing_and_warns(api, tmp_path):
    out = tmp_path / "never"
    response = api.call("generate", domain="retail", scale="small", output_dir=str(out))
    assert response["ok"] and not out.exists()
    assert "output_dir_ignored" in [w["code"] for w in response["warnings"]]


def test_a_directory_that_cannot_be_made_is_an_io_error(api, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    api.fail(
        "generate",
        "io.write_failed",
        domain="retail",
        scale="small",
        format="csv",
        output_dir=str(blocker / "sub"),
    )


@pytest.mark.parametrize(
    "args, code",
    [
        ({"domain": "nope"}, "input.unknown_domain"),
        ({"domain": "retail", "scale": "gigantic"}, "input.invalid_value"),
        ({"domain": "retail", "mode": "star", "profile": "x"}, "input.invalid_value"),
        ({"domain": "/missing.json"}, "input.not_found"),
    ],
)
def test_generate_refusals(api, args, code):
    api.fail("generate", code, **args)


def test_generate_reports_a_broken_foreign_key_as_an_integrity_error(api, monkeypatch):
    from shape.generation.engine import GenerationResult

    monkeypatch.setattr(
        GenerationResult, "verify_integrity", lambda self: ["order.customer_id: 3 orphans"]
    )
    result = api.ok("generate", domain="retail", scale="small")
    assert result["integrity_pass"] is False and result["integrity_errors"] == [
        "order.customer_id: 3 orphans"
    ]


def test_generate_as_a_job_gives_the_same_result(api):
    sync = api.ok("generate", domain="retail", scale="small", seed=9)
    job = api.ok("generate", {"async": True}, domain="retail", scale="small", seed=9)
    assert job["status"] in ("running", "succeeded") and job["job_id"].startswith("job-")
    for _ in range(200):
        state = api.ok("job_status", job_id=job["job_id"])
        if state["status"] != "running":
            break
        time.sleep(0.05)
    assert state["status"] == "succeeded"
    assert state["result"]["tables"] == sync["tables"] and state["result"]["seed"] == 9


# ---- preview --------------------------------------------------------------------------------


def test_preview_returns_the_first_rows_as_json(api):
    result = api.ok("preview", domain="retail", seed=11)
    assert result["domain"] == "retail" and result["seed"] == 11
    customer = result["tables"]["customer"]
    assert customer["preview_rows"] == 5 and len(customer["data"]) == 5
    assert customer["total_rows"] == 1000  # the small preset
    assert customer["columns"][:2] == ["customer_id", "first_name"]
    assert set(customer["data"][0]) == set(customer["columns"])
    assert set(result["tables"]) == set(api.ok("describe", domain="retail")["generation_order"])


def test_preview_rows_match_the_generated_data(api):
    from shape.generation.domains import load_domain
    from shape.generation.engine import Engine

    rows = api.ok("preview", domain="retail", rows=3, seed=2, tables=["customer"])["tables"][
        "customer"
    ]["data"]
    engine = Engine(load_domain("retail").schema, scale="small", seed=2)
    expect = engine.generate().tables["customer"].slice(0, 3).to_pylist()
    assert [r["customer_id"] for r in rows] == [r["customer_id"] for r in expect]
    assert [r["email"] for r in rows] == [r["email"] for r in expect]


def test_preview_dates_are_iso_strings_and_the_whole_result_is_json(api):
    result = api.ok("preview", domain="retail", rows=2, tables=["customer"])
    signup = result["tables"]["customer"]["data"][0]["signup_date"]
    assert isinstance(signup, str) and signup[4] == "-" and "T" in signup


def test_preview_filters_tables_and_refuses_unknown_ones(api):
    assert list(api.ok("preview", domain="retail", tables=["store", "customer"])["tables"]) == [
        "customer",
        "store",
    ]
    e = api.fail("preview", "input.invalid_value", domain="retail", tables=["ghost"])
    assert "ghost" in e["message"]


def test_preview_is_capped(api):
    api.fail("preview", "usage.invalid_argument", domain="retail", rows=10_001)
    assert (
        api.ok("preview", domain="retail", rows=10_000, tables=["store"])["tables"]["store"][
            "preview_rows"
        ]
        == 150
    )


def test_preview_fewer_rows_than_asked_when_the_table_is_small(api):
    store = api.ok("preview", domain="retail", rows=400, tables=["store"])["tables"]["store"]
    assert store["preview_rows"] == store["total_rows"] == 150


def test_a_large_preview_part_is_returned_as_a_file(api, jobs_dir):
    response = api.call(
        "preview",
        {"max_inline_bytes": 1024},
        domain="retail",
        rows=200,
        tables=["customer"],
        seed=1,
    )
    assert response["ok"]
    data = response["result"]["tables"]["customer"]["data"]
    assert data["spilled"] is True and data["bytes"] > 1024
    path = jobs_dir / "bridge" / "results" / (data["content_id"] + ".json")
    assert str(path) == data["path"]
    text = path.read_text()
    assert hashlib.sha256(text.encode()).hexdigest() == data["content_id"]
    rows = json.loads(text)
    assert len(rows) == 200 and rows[0]["customer_id"] == 1
    assert "result_in_file" in [w["code"] for w in response["warnings"]]
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    same = api.ok(
        "preview",
        {"max_inline_bytes": 1024},
        domain="retail",
        rows=200,
        tables=["customer"],
        seed=1,
    )
    assert same["tables"]["customer"]["data"]["content_id"] == data["content_id"]


def test_a_small_preview_part_stays_inline(api, jobs_dir):
    data = api.ok("preview", domain="retail", rows=2, tables=["store"])["tables"]["store"]["data"]
    assert isinstance(data, list) and not (jobs_dir / "bridge" / "results").exists()


def test_preview_unknown_domain_and_seed_type(api):
    api.fail("preview", "input.unknown_domain", domain="nope")
    api.fail("preview", "usage.invalid_argument", domain="retail", seed="1")
