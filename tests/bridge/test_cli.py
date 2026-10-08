"""`shape bridge` as a program: the command line, a real process, and jobs across processes."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import time

from shape.cli.main import main


def run_process(jobs_dir, lines, *extra, timeout=120, env=None):
    import os

    done = subprocess.run(
        [sys.executable, "-m", "shape", "bridge", "--jobs-dir", str(jobs_dir), *extra],
        input="\n".join(json.dumps(x) if not isinstance(x, str) else x for x in lines) + "\n",
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, **(env or {})},
    )
    return done, [json.loads(x) for x in done.stdout.splitlines()]


def test_shape_bridge_once_through_the_cli(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps({"api_version": "1.0", "id": 1, "command": "list"}))
    )
    assert main(["bridge", "--once", "--jobs-dir", str(tmp_path)]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] and doc["id"] == 1 and doc["result"]["count"] >= 1
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"command": "nope"}'))
    assert main(["bridge", "--once", "--jobs-dir", str(tmp_path)]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "usage.unknown_command"


def test_shape_bridge_serves_lines_through_the_cli(monkeypatch, capsys, tmp_path):
    describe = {"id": "b", "command": "describe", "args": {"domain": "retail"}}
    text = '{"id": "a", "command": "list"}\n' + json.dumps(describe) + "\n"
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))
    assert main(["bridge", "--jobs-dir", str(tmp_path)]) == 0
    docs = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert [d["id"] for d in docs] == ["a", "b"] and all(d["ok"] for d in docs)


def test_a_real_process_answers_on_stdout_only(tmp_path):
    done, docs = run_process(
        tmp_path,
        [{"api_version": "1.0", "id": 1, "command": "dry_run", "args": {"domain": "retail"}}],
    )
    assert done.returncode == 0 and len(docs) == 1 and docs[0]["ok"]
    assert done.stdout.count("\n") == 1


def test_jobs_survive_a_restart_of_the_process(tmp_path):
    done, docs = run_process(
        tmp_path,
        [
            {
                "api_version": "1.0",
                "id": 1,
                "command": "generate",
                "args": {"domain": "retail", "scale": "small"},
                "options": {"async": True},
            }
        ],
    )
    job_id = docs[0]["result"]["job_id"]
    # the first process drained its job before it exited; a second one reads the same files
    done, docs = run_process(
        tmp_path,
        [
            {"api_version": "1.0", "id": 2, "command": "job_status", "args": {"job_id": job_id}},
            {"api_version": "1.0", "id": 3, "command": "job_list"},
        ],
    )
    status, listed = docs
    assert (
        status["ok"]
        and status["result"]["status"] == "succeeded"
        and status["result"]["result"]["total_rows"] == 21750
    )
    assert [j["job_id"] for j in listed["result"]["jobs"]] == [job_id]


def test_end_of_input_stops_a_stream_and_says_so(tmp_path):
    start = {
        "api_version": "1.0",
        "id": 1,
        "command": "stream",
        "args": {"domain": "retail", "scale": "small", "interval_seconds": 60, "chunk_size": 20},
    }
    began = time.time()
    done, docs = run_process(tmp_path, [start])
    assert done.returncode == 0 and docs[0]["ok"] and time.time() - began < 100
    stream_id = docs[0]["result"]["stream_id"]
    _, docs = run_process(
        tmp_path,
        [{"api_version": "1.0", "command": "stream_status", "args": {"stream_id": stream_id}}],
    )
    assert docs[0]["result"]["running"] is False and docs[0]["result"]["status"] == "cancelled"


def test_a_job_killed_with_its_process_reads_as_interrupted(tmp_path):
    import os
    import signal

    from scale_schemas import plain_doc

    big = tmp_path / "big.json"
    big.write_text(
        json.dumps(plain_doc({"customer": 100, "order": 40_000_000, "order_line": 40_000_000}))
    )
    request = {
        "api_version": "1.0",
        "id": 1,
        "command": "scale_generate",
        "args": {
            "domain": str(big),
            "scale_mode": "local_single",
            "chunk_size": 100_000,
            "sinks": ["parquet"],
            "sink_config": {"parquet": {"output_dir": str(tmp_path / "out")}},
        },
        "options": {"async": True},
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "shape", "bridge", "--jobs-dir", str(tmp_path / "jobs")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert proc.stdin and proc.stdout

    def ask(doc):
        proc.stdin.write(json.dumps(doc) + "\n")
        proc.stdin.flush()
        return json.loads(proc.stdout.readline())

    try:
        job_id = ask(request)["result"]["job_id"]
        deadline = time.time() + 60
        while time.time() < deadline:
            state = ask(
                {"api_version": "1.0", "command": "job_status", "args": {"job_id": job_id}}
            )["result"]
            assert state["status"] == "running"
            if state["progress"].get("rows_done"):
                break
            time.sleep(0.05)
        assert state["progress"].get("rows_done")
    finally:
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait()
    _, docs = run_process(
        tmp_path / "jobs",
        [{"api_version": "1.0", "command": "job_status", "args": {"job_id": job_id}}],
    )
    state = docs[0]["result"]
    assert state["status"] == "interrupted" and state["error"]["code"] == "input.job_interrupted"
    assert state["result"] is None and state["progress"].get("rows_done")  # how far it got is kept


def test_startup_stays_light(tmp_path):
    code = (
        "import sys; from shape.cli.main import main; main(['version']); "
        "print(sorted(m for m in sys.modules if m.startswith('shape.bridge')))"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert done.stdout.strip().endswith("[]"), done.stdout
