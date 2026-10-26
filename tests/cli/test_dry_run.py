"""W1-14 deliverable 6: ``--dry-run`` on every core command that writes.

Each case runs the command with ``--dry-run`` against a world built with the real commands, with
the network blocked, and asserts the whole folder (and the state folders) are byte for byte
unchanged, the exit code is 0 and the planned actions are well formed."""

from __future__ import annotations

import hashlib
import json
import random
import socket
import subprocess
from pathlib import Path

import pytest

from shape.cli import machine
from shape.cli.main import main

ACTIONS = {"write", "create", "delete", "send"}


def _snapshot(root: Path) -> dict[str, str]:
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        out[rel] = "dir" if p.is_dir() else hashlib.sha256(p.read_bytes()).hexdigest()
    return out


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("world")
    mp = pytest.MonkeyPatch()
    mp.chdir(root)
    for name, sub in (
        ("SHAPE_HOME", "home"),
        ("SHAPE_JOBS_DIR", "jobs"),
        ("SHAPE_PROFILE_REGISTRY", "preg"),
        ("HOME", "home2"),
    ):
        mp.setenv(name, str(root / sub))
    (root / "home2").mkdir()
    rng = random.Random(1)
    for name, mean in (("a", 10), ("b", 30)):
        rows = "\n".join(f"{i},{rng.gauss(mean, 2):.2f}" for i in range(200))
        (root / f"{name}.csv").write_text("id,amt\n" + rows + "\n")
    (root / "c.json").write_text(json.dumps({"columns": {"amt": {}}}))
    (root / "t.sql").write_text("CREATE TABLE t (id INT PRIMARY KEY, name VARCHAR(20));\n")
    (root / "design.json").write_text(
        json.dumps({"format": "shape-design", "version": 1, "name": "x", "entities": []})
    )
    (root / "drift.plan.json").write_text(json.dumps({"start": "2026-03-01", "days": 2}))
    (root / "pack.yaml").write_text("id: x\nkind: stream\ndomain: hr\n")
    (root / "r.json").write_text(
        json.dumps({"format": "shape-result", "version": 1, "command": "diff", "exit_code": 0})
    )
    (root / "comment.md").write_text("<!-- shape-pr-comment -->\nbody\n")
    (root / "x-1.0-py3-none-any.whl").write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    (root / "s.schema.json").write_text(
        json.dumps({"type": "object", "properties": {"id": {"type": "integer"}}})
    )
    (root / "gitrepo").mkdir()
    subprocess.run(["git", "init", "-q", str(root / "gitrepo")], check=True)
    real = [
        ["profile", "a.csv", "-o", "a.shape"],
        ["profile", "b.csv", "-o", "b.shape"],
        ["from-ddl", "t.sql", "-o", "t.gen.json"],
        ["keygen", "k1", "--no-passphrase"],
        ["generate", "retail", "--scale", "small", "-f", "csv", "-o", "prev"],
        ["profile", "export", "a.shape", "-o", "ex.json"],
        ["proposals", "propose", "a.shape", "-d", "dec.json"],
        ["registry", "reg", "commit", "nm", "a.shape", "--allow-raw"],
        ["profile", "registry", "save", "a.shape", "--system", "crm", "--name", "v1"],
    ]
    for argv in real:
        assert main(argv) == 0, argv
    from shape.scale.jobs import JobRecord, JobStore

    (root / "jobs").mkdir(exist_ok=True)
    JobStore(root / "jobs").put(JobRecord(job_id="job-1", kind="generate", status="succeeded"))
    yield root
    mp.undo()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a socket was opened during --dry-run")

    for name in ("connect", "connect_ex", "bind"):
        monkeypatch.setattr(socket.socket, name, refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


CASES = {
    "capture": ["capture", "a.csv", "-o", "new/m.shape"],
    "profile": ["profile", "a.csv", "-o", "new/p.shape", "--html", "new/p.html"],
    "stream-profile": ["stream-profile", "kafka://localhost:9092/topic", "-o", "new/sp.shape"],
    "diff": ["diff", "a.shape", "b.shape", "--junit", "new/r.xml", "--sarif", "new/r.sarif"],
    "check": ["check", "a.shape", "c.json", "--junit", "new/r.xml"],
    "verify": ["verify", "a.csv", "-o", "new/rep.json", "--junit", "new/v.xml"],
    "fidelity": ["fidelity", "a.csv", "b.csv", "-o", "new/f.json"],
    "design": ["design", "design.json", "-o", "new/star.sql"],
    "from-ddl": ["from-ddl", "t.sql", "-o", "new/t.gen.json"],
    "keygen": ["keygen", "new/k2", "--no-passphrase"],
    "sign": ["sign", "a.shape", "--key", "k1.key"],
    "learn": ["learn", "a.csv", "-o", "new/l.json"],
    "emit": ["emit", "retail", "--table", "customer", "--sink", "file", "-o", "new/ev.jsonl"],
    "stream": ["stream", "retail", "-t", "customer", "--sink", "file", "-o", "new/s.jsonl"],
    "mask": ["mask", "a.csv", "-o", "new/m.csv"],
    "continue": ["continue", "retail", "--input", "prev", "-o", "new/cont"],
    "time-travel": ["time-travel", "retail", "-o", "new/tt"],
    "chaos": ["chaos", "retail", "--input", "prev", "-o", "new/ch", "--corrupt", "duplicates"],
    "generate-drift": ["generate-drift", "t.gen.json", "drift.plan.json", "-o", "new/gd"],
    "pack run": ["pack", "run", "pack.yaml", "-o", "new/pr"],
    "transform star": ["transform", "star", "retail", "-o", "new/st"],
    "transform cdm": ["transform", "cdm", "retail", "-o", "new/cdm"],
    "jobs cancel": ["jobs", "cancel", "job-1", "--jobs-dir", "jobs"],
    "jobs resume": ["jobs", "resume", "job-1", "--jobs-dir", "jobs"],
    "proposals propose": ["proposals", "propose", "a.shape", "-d", "new/dec.json"],
    "proposals decide": ["proposals", "decide", "-d", "dec.json", "some-id", "accept"],
    "bridge schema": ["bridge", "schema", "--out", "new/bs"],
    "demo init": ["demo", "init", "--name", "dev"],
    "demo notebook": ["demo", "notebook", "retail", "--output", "new/nb.ipynb"],
    "demo report": ["demo", "report", "sess1", "--output", "new/rep.md"],
    "drift": ["drift", "a.csv", "b.csv", "-o", "new/d.json"],
    "registry commit": ["registry", "reg", "commit", "nm2", "a.shape", "--allow-raw"],
    "registry checkout": ["registry", "reg", "checkout", "nm", "latest", "-o", "new/co.shape"],
    "registry tag": ["registry", "reg", "tag", "nm", "v9"],
    "registry promote": ["registry", "reg", "promote", "nm", "latest", "prod"],
    "init": ["init", "new/proj2"],
    "git-setup": ["git-setup", "--repo", "gitrepo"],
    "profile export": ["profile", "export", "a.shape", "-o", "new/ex.json"],
    "profile import": ["profile", "import", "ex.json", "-o", "new/im.shape"],
    "profile safe": ["profile", "safe", "a.shape", "-o", "new/safe.json"],
    "profile validate": ["profile", "validate", "--safe", "a.shape", "--junit", "new/lv.xml"],
    "profile registry save": [
        "profile",
        "registry",
        "save",
        "a.shape",
        "--system",
        "crm",
        "--name",
        "v2",
    ],  # fmt: skip
    "profile registry delete": ["profile", "registry", "delete", "crm/a/v1"],
    "profile registry tag": ["profile", "registry", "tag", "crm/a/v1", "extra"],
    "profile registry reindex": ["profile", "registry", "reindex"],
    "ci comment": ["ci", "comment", "r.json", "-o", "new/comment.md"],
    "ci post-comment": [
        "ci",
        "post-comment",
        "--body-file",
        "comment.md",
        "--repo",
        "o/r",
        "--pr",
        "7",
    ],  # fmt: skip
    "badge": ["badge", "r.json", "-o", "new/badge.svg"],
    "plugins new": ["plugins", "new", "acme-src", "--group", "shape.sources", "-o", "new/plug"],
    # INT-18: the writing commands of the lanes merged beside W1-14
    "plugins sign": ["plugins", "sign", "x-1.0-py3-none-any.whl", "--key", "k1.key", "-o", "new/s"],
    "plugins allowlist init": ["plugins", "allowlist", "init", "-o", "new/allow.json"],
    "dictionary": ["dictionary", "a.shape", "--no-project", "-o", "new/dict.md"],
    "contract emit": ["contract", "emit", "c.json", "--to", "ddl", "-o", "new/c.sql"],
    "pin": ["pin", "t.gen.json", "-o", "new/pinned.gen.json"],
    "import-schema": ["import-schema", "s.schema.json", "-o", "new/i.gen.json"],
    "scorecard": ["scorecard", "a.csv", "-o", "new/score.json"],
    "skew": ["skew", "a.csv", "b.csv", "-o", "new/skew.json"],
    "resolve synth": ["resolve", "synth", "a.csv", "-o", "new/dups.csv", "--truth", "new/t.csv"],
    "rules mutate": ["rules", "mutate", "a.csv", "c.json", "-o", "new/mut.json"],
    "rules backtest": ["rules", "backtest", "reg", "nm", "c.json", "-o", "new/bt.json"],
    "suite run": ["suite", "run", "smoke", "-o", "new/suite"],
    "proposals contract": ["proposals", "contract", "-d", "dec.json", "-o", "new/pc.json"],
    "report-card": ["report-card", "a.csv", "b.csv", "-o", "new/card.json"],
    "share-bundle create": [
        "share-bundle",
        "create",
        "prev",
        "--source",
        "prev",
        "--classifications",
        "c.json",
        "-o",
        "new/bundle.zip",
    ],  # fmt: skip
    "skew-rehearsal": [
        "skew-rehearsal",
        "a.shape",
        "--schema",
        "retail",
        "--scale",
        "small",
        "-o",
        "new/rh",
    ],  # fmt: skip
    "timelapse": ["timelapse", "reg", "nm", "--column", "amt", "-o", "new/tl.json"],
    "contracts check-consumers": [
        "contracts",
        "check-consumers",
        "a.shape",
        "--no-project",
        "-o",
        "new/cc.json",
    ],  # fmt: skip
    "parity": ["parity", "a.csv", "b.csv", "--no-project", "-o", "new/parity.json"],
    "profile merge": ["profile", "merge", "a.shape", "b.shape", "-o", "new/m.shape"],
    # W6-03
    "detective start": ["detective", "start", "first-case", "-o", "new/case"],
    "library get": ["library", "get", "iris", "-o", "new/iris.shape"],
}


def test_every_writing_command_has_a_case():
    assert set(CASES) == set(machine.SPECS)


def _json_flag(path: str) -> list[str]:
    """``--json`` for a command where it is a switch, ``--json -`` where it names a file."""
    from shape.cli.introspect import core_commands

    parser = next(c.parser for c in core_commands() if c.path == path)
    act = next(a for a in parser._actions if "--json" in a.option_strings)
    return ["--json", "-"] if act.nargs != 0 else ["--json"]


def _action_lines(out: str) -> list[str]:
    return [ln for ln in out.splitlines() if ln.startswith("would ")]


@pytest.mark.parametrize("path", sorted(CASES))
def test_dry_run_changes_nothing_opens_no_socket_and_lists_actions(path, world, capsys):
    before = _snapshot(world)
    assert main([*CASES[path], "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert _snapshot(world) == before
    assert _action_lines(out) or "nothing would be written" in out


@pytest.mark.parametrize("path", sorted(CASES))
def test_dry_run_json_is_one_dry_run_document(path, world, capsys):
    before = _snapshot(world)
    assert main([*CASES[path], "--dry-run", *_json_flag(path)]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert _snapshot(world) == before
    assert doc["format"] == "shape-dry-run" and doc["version"] == 1
    assert doc["command"] == path
    assert isinstance(doc["actions"], list)
    for act in doc["actions"]:
        assert set(act) == {"action", "target"} and act["action"] in ACTIONS
        assert isinstance(act["target"], str) and act["target"]


def test_dry_run_json_exit_code_is_zero(world, capsys):
    assert main(["capture", "a.csv", "-o", "new/m.shape", "--dry-run", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["actions"] == [
        {"action": "create", "target": str(Path("new/m.shape"))}
    ]


def test_existing_files_are_write_and_new_ones_are_create(world, capsys):
    assert main(["capture", "a.csv", "-o", "a.shape", "--dry-run", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["actions"][0] == {
        "action": "write",
        "target": "a.shape",
    }


def test_a_sink_is_a_send_with_the_secret_redacted(world, capsys):
    argv = ["emit", "retail", "--sink", "kafka://user:hunter2@broker:9092/topic", "--dry-run"]
    assert main([*argv, "--json"]) == 0
    text = capsys.readouterr().out
    assert "hunter2" not in text
    acts = json.loads(text)["actions"]
    assert acts and acts[0]["action"] == "send" and "broker:9092" in acts[0]["target"]
    from shape.generation.domains import load_domain

    schema = load_domain("retail").schema.to_dict()
    for table in schema["tables"].values():
        for column in table["columns"].values():
            if column["type"] == "decimal":
                column["precision"] = 18
                column["scale"] = 2
    path = world / "explicit-sql-schema.json"
    path.write_text(json.dumps(schema))
    argv = ["generate", str(path), "--to", "mssql://sa:hunter2@db.example/prod", "--dry-run"]
    assert main(argv) in (0, 1)  # generate's own dry run: its plan, never a connection
    assert "hunter2" not in capsys.readouterr().out


def test_secrets_in_flags_are_never_echoed(world, capsys):
    argv = ["emit", "retail", "--sink", "eventhubs://ns.example/hub", "--client-secret", "hunter2"]
    # emit's own dry run (W2-09) refuses a literal secret as the real run does (exit 2)
    assert main([*argv, "--dry-run", "--json"]) == 2
    captured = capsys.readouterr()
    assert "hunter2" not in captured.out and "hunter2" not in captured.err


def test_delete_action_for_profile_registry_delete(world, capsys):
    assert main([*CASES["profile registry delete"], "--dry-run", "--json"]) == 0
    acts = json.loads(capsys.readouterr().out)["actions"]
    assert acts[0]["action"] == "delete" and acts[0]["target"].endswith("v1.shape")


def test_jobs_resume_of_a_fabric_job_is_also_a_send(world, capsys):
    from shape.scale.jobs import JobRecord, JobStore

    JobStore(world / "jobs").put(
        JobRecord(job_id="job-f", kind="generate", status="failed", fabric={"fabric_run_id": "x"})
    )
    try:
        assert main(["jobs", "resume", "job-f", "--jobs-dir", "jobs", "--dry-run", "--json"]) == 0
        acts = json.loads(capsys.readouterr().out)["actions"]
        assert {a["action"] for a in acts} == {"send", "write"}
    finally:
        (world / "jobs" / "job-f.json").unlink()


# ---- invalid input: exit 2, nothing written -----------------------------------------------------

BAD = {
    "capture": ["capture", "missing.csv", "-o", "new/m.shape"],
    "profile": ["profile", "missing.csv", "-o", "new/p.shape"],
    "diff": ["diff", "missing.shape", "b.shape", "--junit", "new/r.xml"],
    "check": ["check", "a.shape", "missing.json", "--junit", "new/r.xml"],
    "verify": ["verify", "missing_dir", "-o", "new/rep.json"],
    "fidelity": ["fidelity", "missing.csv", "b.csv", "-o", "new/f.json"],
    "from-ddl": ["from-ddl", "missing.sql", "-o", "new/t.gen.json"],
    "keygen": ["keygen", "k1", "--no-passphrase"],  # k1.key exists: never overwritten
    "sign": ["sign", "a.shape", "--key", "missing.key"],
    "mask": ["mask", "missing.csv", "-o", "new/m.csv"],
    "drift": ["drift", "a.csv", "missing.csv", "-o", "new/d.json"],
    "jobs cancel": ["jobs", "cancel", "no-such-job", "--jobs-dir", "jobs"],
    "proposals decide": ["proposals", "decide", "-d", "missing.json", "id", "accept"],
    "registry commit": ["registry", "reg", "commit", "nm3", "missing.shape"],
    "init": ["init", "gitrepo", "--source", "bad name=x"],
    "git-setup": ["git-setup", "--repo", "missing_dir"],
    "profile export": ["profile", "export", "missing.shape", "-o", "new/ex.json"],
    "profile registry delete": ["profile", "registry", "delete", "crm/none/v1"],
}


@pytest.mark.parametrize("path", sorted(BAD))
def test_invalid_input_exits_two_and_writes_nothing(path, world, capsys):
    before = _snapshot(world)
    assert main([*BAD[path], "--dry-run"]) == 2
    assert _snapshot(world) == before
    assert "shape: error" in capsys.readouterr().err


def test_invalid_input_with_json_still_prints_a_document(world, capsys):
    assert main(["capture", "missing.csv", "-o", "new/m.shape", "--dry-run", "--json"]) == 2
    doc = json.loads(capsys.readouterr().out)
    assert (
        doc["format"] == "shape-dry-run" and doc["actions"] == [] and "missing.csv" in doc["error"]
    )


def test_an_output_below_a_file_is_invalid(world, capsys):
    assert main(["capture", "a.csv", "-o", "a.csv/m.shape", "--dry-run"]) == 2
    assert "not a folder" in capsys.readouterr().err


def test_commands_with_their_own_dry_run_keep_it(world, capsys):
    before = _snapshot(world)
    assert main(["generate", "retail", "--scale", "small", "--dry-run", "-o", "new/g"]) == 0
    assert "customer" in capsys.readouterr().out  # the plan table, as before
    assert _snapshot(world) == before


def test_without_the_flag_the_command_still_writes(world):
    assert main(["capture", "a.csv", "-o", "new_real.shape"]) == 0
    assert (world / "new_real.shape").is_file()
    (world / "new_real.shape").unlink()


def test_git_setup_outside_a_repository_is_invalid(world, capsys):
    (world / "not_a_repo").mkdir(exist_ok=True)
    assert main(["git-setup", "--repo", "not_a_repo", "--dry-run"]) == 2
    assert "git" in capsys.readouterr().err


def test_a_file_sink_lists_its_checkpoint(world, capsys):
    argv = ["emit", "retail", "--sink", "file", "-o", "new/ev.jsonl", "--dry-run", "--json"]
    assert main(argv) == 0
    targets = [a["target"] for a in json.loads(capsys.readouterr().out)["actions"]]
    assert targets == [str(Path("new/ev.jsonl")), str(Path("new/ev.jsonl.checkpoint"))]


def test_demo_notebook_lists_its_default_file_name(world, capsys):
    assert main(["demo", "notebook", "retail", "--dry-run", "--json"]) == 0
    acts = json.loads(capsys.readouterr().out)["actions"]
    assert acts == [{"action": "create", "target": "shape_retail_inference.ipynb"}]


def test_ci_defaults_of_shape_yml_are_listed_as_writes(world, capsys, monkeypatch):
    proj = world / "proj_ci"
    proj.mkdir(exist_ok=True)
    (proj / "shape.yml").write_text(
        "format: shape-project\nversion: 1\nsources:\n  a: {path: x}\n"
        "ci: {junit: 'out/{command}.xml', json: 'out/{command}.json'}\n"
    )
    monkeypatch.chdir(proj)
    try:
        assert main(["diff", "../a.shape", "../b.shape", "--dry-run", "--json", "-"]) == 0
        acts = json.loads(capsys.readouterr().out)["actions"]
        assert {"action": "create", "target": str(proj / "out" / "diff.xml")} in acts
        assert {"action": "create", "target": str(proj / "out" / "diff.json")} in acts
    finally:
        monkeypatch.chdir(world)
        (proj / "shape.yml").unlink()
        proj.rmdir()
