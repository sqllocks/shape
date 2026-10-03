"""W7-05 item 6 (part): the bridge command ``chaos`` and W1-17's input check."""

from __future__ import annotations

import json
import threading
import time

import pytest

from shape.cli.main import main


@pytest.fixture
def generated(tmp_path, schema_file, capsys):
    """A folder of Shape-generated tables, marked by the provenance sidecar `shape generate`
    writes. (The bridge's `generate` writes no sidecar, so its files are not marked.)"""
    out = tmp_path / "gen"
    assert main(["generate", str(schema_file), "-f", "csv", "-o", str(out), "--seed", "3"]) == 0
    capsys.readouterr()
    return out


def files_under(folder):
    return sorted(p.name for p in folder.rglob("*") if p.is_file()) if folder.exists() else []


def log_header(path):
    return json.loads(path.read_text().splitlines()[0])


def cli_json(capsys, *argv):
    code = main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def wait(api, job_id, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = api.ok("job_status", job_id=job_id)
        if status["status"] not in ("running", "submitted"):
            return status
        time.sleep(0.05)
    raise AssertionError("the job did not end")


# ---- the result is the one `shape chaos --json` prints ---------------------------------------


def test_chaos_on_a_generated_folder_gives_what_the_cli_prints(api12, generated, tmp_path, capsys):
    out_b, out_c = tmp_path / "bridge", tmp_path / "cli"
    result = api12.ok(
        "chaos",
        output_dir=str(out_b),
        input=str(generated),
        corrupt=["duplicates=0.05", "negative_amounts=0.1@order_line.amount"],
        seed=7,
    )
    code, cli = cli_json(
        capsys, "chaos", "--input", str(generated), "-o", str(out_c), "--seed", "7",
        "--corrupt", "duplicates=0.05", "--corrupt", "negative_amounts=0.1@order_line.amount", "--json",
    )  # fmt: skip
    assert code == 0
    normal = lambda d, root: json.loads(json.dumps(d).replace(str(root), "OUT"))  # noqa: E731
    assert normal(result, out_b) == normal(cli, out_c)
    assert files_under(out_b) == files_under(out_c)
    assert result["changes"] > 0 and result["seed"] == 7 and result["batch"] == 0
    assert result["ground_truth"] == str(out_b / "_chaos_ground_truth.jsonl")
    assert (out_b / "_chaos_ground_truth.jsonl").read_bytes() == (
        out_c / "_chaos_ground_truth.jsonl"
    ).read_bytes()
    assert {Path_(f).name for f in result["files"]} == {
        "customer.csv",
        "order.csv",
        "order_line.csv",
    }


def Path_(value):  # noqa: N802
    from pathlib import Path

    return Path(value)


def test_the_same_seed_gives_the_same_log_and_formats_and_batch_are_honoured(
    api12, generated, tmp_path
):
    args = {"input": str(generated), "corrupt": ["duplicates=0.1"], "seed": 3}
    one = api12.ok("chaos", output_dir=str(tmp_path / "a"), **args)
    two = api12.ok("chaos", output_dir=str(tmp_path / "b"), **args)
    assert (tmp_path / "a" / "_chaos_ground_truth.jsonl").read_bytes() == (
        tmp_path / "b" / "_chaos_ground_truth.jsonl"
    ).read_bytes() and one["changes"] == two["changes"]
    parquet = api12.ok("chaos", output_dir=str(tmp_path / "p"), format="parquet", batch=2, **args)
    assert all(f.endswith(".parquet") for f in parquet["files"]) and parquet["batch"] == 2
    custom = tmp_path / "elsewhere" / "truth.jsonl"
    custom.parent.mkdir()
    named = api12.ok("chaos", output_dir=str(tmp_path / "c"), ground_truth=str(custom), **args)
    assert named["ground_truth"] == str(custom) and custom.is_file()
    assert not (tmp_path / "c" / "_chaos_ground_truth.jsonl").exists()


def test_chaos_generates_when_there_is_no_input(api12, schema_file, tmp_path):
    result = api12.ok(
        "chaos", output_dir=str(tmp_path / "out"), domain=str(schema_file),
        ground_truth=str(tmp_path / "first.jsonl"), corrupt=["duplicates=0.05"], seed=5,
    )  # fmt: skip
    assert result["changes"] > 0 and len(result["files"]) == 3
    # what it wrote is marked as Shape-generated, so it can be corrupted again (the log is kept
    # outside the folder: a `.jsonl` in it would be read as a table)
    again = api12.ok(
        "chaos", output_dir=str(tmp_path / "out2"), input=str(tmp_path / "out"),
        corrupt=["duplicates=0.05"],
    )  # fmt: skip
    assert again["changes"] > 0


# ---- W1-17's input check, unchanged -----------------------------------------------------------


def test_an_unmarked_input_is_refused_and_nothing_is_written(api12, tmp_path):
    src, out = tmp_path / "real", tmp_path / "chaos"
    src.mkdir()
    (src / "orders.csv").write_text("id,amount\n1,5\n2,6\n3,7\n4,8\n")
    error = api12.fail(
        "chaos", "policy.unverified_input", output_dir=str(out), input=str(src),
        corrupt=["duplicates=0.5"],
    )  # fmt: skip
    assert "is not marked as Shape-generated" in error["message"] and error["hint"]
    assert not out.exists()  # not even the folder
    assert files_under(src) == ["orders.csv"]  # the input is untouched, no sidecar is written to it


def test_one_unmarked_file_among_generated_ones_refuses_the_folder(api12, generated, tmp_path):
    (generated / "extra.csv").write_text("a\n1\n")
    api12.fail(
        "chaos", "policy.unverified_input", output_dir=str(tmp_path / "o"), input=str(generated),
        corrupt=["duplicates=0.05"],
    )  # fmt: skip
    assert not (tmp_path / "o").exists()


def test_a_file_edited_after_generation_is_refused(api12, generated, tmp_path):
    target = generated / "customer.csv"
    target.write_text(target.read_text() + "999999,x,y\n")
    error = api12.fail(
        "chaos", "policy.unverified_input", output_dir=str(tmp_path / "o"), input=str(generated),
        corrupt=["duplicates=0.05"],
    )  # fmt: skip
    assert "sha256 differs" in error["message"]


def test_allow_real_input_runs_with_the_warning_and_logs_unverified(api12, tmp_path):
    src, out = tmp_path / "real", tmp_path / "chaos"
    src.mkdir()
    (src / "orders.csv").write_text("id,amount\n1,5\n2,6\n3,7\n4,8\n")
    response = api12.call(
        "chaos", output_dir=str(out), input=str(src), corrupt=["duplicates=0.5"],
        allow_real_input=True,
    )  # fmt: skip
    assert response["ok"], response
    assert [w["code"] for w in response["warnings"]] == ["real_input_corrupted"]
    assert log_header(out / "_chaos_ground_truth.jsonl")["input_provenance"] == "unverified"


def test_allow_real_input_on_verified_input_does_not_warn(api12, generated, tmp_path):
    response = api12.call(
        "chaos", output_dir=str(tmp_path / "o"), input=str(generated),
        corrupt=["duplicates=0.05"], allow_real_input=True,
    )  # fmt: skip
    assert response["ok"] and response["warnings"] == []
    assert (
        log_header(tmp_path / "o" / "_chaos_ground_truth.jsonl")["input_provenance"] == "verified"
    )


def test_the_output_may_not_be_the_input_folder(api12, generated):
    api12.fail("chaos", "input.invalid_value", output_dir=str(generated), input=str(generated),
               corrupt=["duplicates=0.05"])  # fmt: skip
    api12.fail("chaos", "input.invalid_value", output_dir=str(generated / "sub"),
               input=str(generated), corrupt=["duplicates=0.05"])  # fmt: skip
    assert files_under(generated).count("_chaos_ground_truth.jsonl") == 0


# ---- errors ----------------------------------------------------------------------------------


def test_chaos_errors(api12, generated, schema_file, tmp_path):
    out = str(tmp_path / "o")
    ok = {"output_dir": out, "input": str(generated), "corrupt": ["duplicates=0.05"]}
    api12.fail(
        "chaos", "usage.missing_argument", **{k: v for k, v in ok.items() if k != "output_dir"}
    )
    api12.fail("chaos", "usage.missing_argument", **{k: v for k, v in ok.items() if k != "corrupt"})
    api12.fail("chaos", "usage.invalid_argument", **{**ok, "corrupt": "duplicates=0.05"})
    api12.fail("chaos", "usage.invalid_argument", **{**ok, "format": "xlsx"})
    api12.fail("chaos", "usage.invalid_argument", **{**ok, "batch": -1})
    api12.fail("chaos", "usage.invalid_argument", **{**ok, "mode": "snowflake"})
    api12.fail("chaos", "input.not_found", **{**ok, "input": str(tmp_path / "none")})
    api12.fail(
        "chaos", "input.invalid_value", output_dir=out, corrupt=["duplicates=0.05"]
    )  # no source
    api12.fail("chaos", "input.invalid_value", **{**ok, "corrupt": []})
    api12.fail("chaos", "input.invalid_value", **{**ok, "corrupt": ["nonsense=0.1"]})
    api12.fail("chaos", "input.invalid_value", **{**ok, "corrupt": ["duplicates=5"]})
    api12.fail("chaos", "input.invalid_value", **{**ok, "start_date": "2026-03-01"})
    api12.fail("chaos", "input.invalid_value", **{**ok, "batch": 1, "start_date": "2026-03-01"})
    api12.fail("chaos", "input.unknown_domain", output_dir=out, domain="no_such_domain",
               corrupt=["duplicates=0.05"])  # fmt: skip
    api12.fail("chaos", "input.invalid_value", output_dir=out, domain=str(schema_file),
               scale="gigantic", corrupt=["duplicates=0.05"])  # fmt: skip
    assert not (tmp_path / "o").exists()  # a refused request wrote nothing


def test_a_destination_that_is_not_local_is_not_permitted(api12, generated, tmp_path):
    ok = {"input": str(generated), "corrupt": ["duplicates=0.05"]}
    for target in ("abfss://c@acct.dfs.core.windows.net/x", "s3://bucket/out", "https://h/x"):
        api12.fail("chaos", "policy.not_permitted", output_dir=target, **ok)
    api12.fail("chaos", "policy.not_permitted", output_dir=str(tmp_path / "o"),
               ground_truth="s3://bucket/log.jsonl", **ok)  # fmt: skip
    assert not (tmp_path / "o").exists()


def test_an_unwritable_destination_is_io_write_failed(api12, generated, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a folder")
    api12.fail("chaos", "io.write_failed", output_dir=str(blocker / "sub"), input=str(generated),
               corrupt=["duplicates=0.05"])  # fmt: skip


# ---- job, cancel -----------------------------------------------------------------------------


def test_chaos_runs_as_a_job(api12, generated, tmp_path):
    started = api12.ok(
        "chaos", {"async": True}, output_dir=str(tmp_path / "o"), input=str(generated),
        corrupt=["duplicates=0.05"],
    )  # fmt: skip
    assert started["cancellable"] is True
    done = wait(api12, started["job_id"])
    assert done["status"] == "succeeded" and done["result"]["changes"] > 0
    bad = api12.call("chaos", {"async": True}, output_dir="s3://x/y", input=str(generated),
                     corrupt=["duplicates=0.05"])  # fmt: skip
    assert bad["error"]["code"] == "policy.not_permitted"
    assert [j["status"] for j in api12.ok("job_list")["jobs"]] == ["succeeded"]  # no failed job


def test_a_cancelled_chaos_job_is_cancelled_and_writes_nothing(
    api12, generated, tmp_path, monkeypatch
):
    import shape.chaos.groundtruth as ground

    real = ground.corrupt_tables
    inside = threading.Event()

    def slow(*a, **k):
        inside.set()
        time.sleep(1.5)  # the cancel request arrives while the corruption runs
        return real(*a, **k)

    monkeypatch.setattr(ground, "corrupt_tables", slow)
    out = tmp_path / "o"
    started = api12.ok(
        "chaos",
        {"async": True},
        output_dir=str(out),
        input=str(generated),
        corrupt=["duplicates=0.05"],
    )
    assert inside.wait(30)
    api12.ok("job_cancel", job_id=started["job_id"])
    status = wait(api12, started["job_id"])
    assert status["status"] == "cancelled" and status["result"]["files"] == 0
    assert status["result"]["stage"] == "writing"
    assert not out.exists()  # nothing was written


def test_effects_and_annotations():
    from shape.bridge.registry import COMMANDS

    command = COMMANDS["chaos"]
    assert command.job and command.cancellable and command.since == "1.2"
    assert command.effects == ("reads_files", "writes_files", "cancels")
    assert {k: a.path for k, a in command.args.items() if a.path} == {
        "output_dir": "write",
        "input": "read",
        "ground_truth": "write",
    }
    assert command.args["domain"].name_or_path
