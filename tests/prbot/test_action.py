"""W6-01 deliverables 3 and 4: the composite action (`action.yml`) and its self-test workflow.

The hardening tests parse the YAML. The behaviour tests run the action's own `run:` scripts in
bash, with the environment GitHub would give them, on the fixture projects of
`tests/fixtures/action/` (the install and Python steps are the only ones left out)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from w6_stub import TOKEN, Stub

ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / "action.yml"
FIXTURES = ROOT / "tests" / "fixtures" / "action"
SELFTEST = ROOT / ".github" / "workflows" / "action-selftest.yml"
STAGED = ROOT / "docs" / "plans" / "lane_status" / "W6-01.action-selftest.yml"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="the action's scripts need bash")


def _load(path: Path) -> dict[str, Any]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(doc, dict)
    return doc


def _steps() -> list[dict[str, Any]]:
    return list(_load(ACTION)["runs"]["steps"])


def _step(name: str) -> dict[str, Any]:
    return next(s for s in _steps() if s.get("name") == name)


# ---- the interface -------------------------------------------------------------------------


def test_it_is_a_composite_action_at_the_repository_root():
    doc = _load(ACTION)
    assert doc["runs"]["using"] == "composite"
    assert doc["name"] and doc["description"]


def test_inputs_and_outputs_are_the_documented_ones():
    doc = _load(ACTION)
    assert set(doc["inputs"]) == {
        "shape-version",
        "project",
        "sources",
        "fail-on",
        "comment",
        "token",
        "python-version",
    }
    defaults = {k: v.get("default") for k, v in doc["inputs"].items()}
    assert defaults["fail-on"] == "fail" and defaults["comment"] == "true"
    assert defaults["project"] == "." and defaults["token"] == "${{ github.token }}"
    assert defaults["sources"] == "" and defaults["shape-version"] == ""
    assert set(doc["outputs"]) == {"verdict", "findings", "comment-path"}
    for out in doc["outputs"].values():
        assert out["value"].startswith("${{ steps.") and out["description"]
    for spec in doc["inputs"].values():
        assert spec["description"]


# ---- hardening -----------------------------------------------------------------------------


def test_every_action_is_pinned_to_a_full_commit_sha():
    uses = [s["uses"] for s in _steps() if "uses" in s]
    assert uses, "the action sets Python up with an action"
    for ref in uses:
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref), ref


def test_the_self_test_workflow_pins_its_actions_too():
    steps = yaml.safe_load(_selftest_text())["jobs"]["selftest"]["steps"]
    for ref in (s["uses"] for s in steps if "uses" in s):
        if ref == "./":
            continue
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref), ref


def test_inputs_reach_scripts_only_through_env():
    for step in _steps():
        script = step.get("run", "")
        assert "${{" not in script, f"{step.get('name')}: interpolation inside a script"
        assert "github.event" not in script
    env_values = " ".join(str(v) for s in _steps() for v in (s.get("env") or {}).values())
    for name in ("inputs.sources", "inputs.project", "inputs.fail-on", "inputs.shape-version"):
        assert f"${{{{ {name} }}}}" in env_values


def test_no_step_echoes_the_token():
    for step in _steps():
        script = step.get("run", "")
        env = step.get("env") or {}
        assert "inputs.token" not in script
        assert "set -x" not in script and "xtrace" not in script and "set -o xtrace" not in script
        assert not re.search(r"\b(printenv|env\s*\|)", script)
        for name, value in env.items():
            if "inputs.token" in str(value):
                assert name == "GITHUB_TOKEN"
                assert not re.search(rf"(echo|printf)[^\n]*\$\{{?{name}\b", script)
                assert "$GITHUB_TOKEN" not in script  # the CLI reads it from the environment
        assert "github.token" not in script


def test_the_token_is_given_to_one_step_only():
    holders = [s["name"] for s in _steps() if "inputs.token" in str(s.get("env") or {})]
    assert holders == ["Check the project"]


def test_the_documents_and_examples_use_pull_request_never_the_target_event():
    files = [ROOT / "docs" / n for n in ("PR_BOT.md", "CI.md")] + [ROOT / "README.md"]
    files += sorted((ROOT / "examples").rglob("*.y*ml"))
    files += [STAGED if STAGED.exists() else SELFTEST]
    checked = 0
    for path in files:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        checked += 1
        in_block = False
        for number, line in enumerate(text.splitlines(), 1):
            if line.strip().startswith("```"):
                in_block = not in_block
            if "pull_request_target" in line:
                assert not in_block and path.suffix == ".md", f"{path}:{number}"
                assert re.search(r"\b(never|not|unsafe|dangerous)\b", line, re.I), (
                    f"{path}:{number}"
                )
    assert checked >= 2
    assert "pull_request" in (ROOT / "docs" / "PR_BOT.md").read_text(encoding="utf-8")


def test_the_scaffold_workflow_of_shape_init_uses_pull_request():
    from shape.project import scaffold

    assert "pull_request_target" not in scaffold._WORKFLOW


# ---- the self-test workflow ----------------------------------------------------------------


def _selftest_text() -> str:
    path = SELFTEST if SELFTEST.exists() else STAGED
    return path.read_text(encoding="utf-8")


def test_the_self_test_runs_the_action_on_pull_requests_against_both_fixtures():
    doc = yaml.safe_load(_selftest_text())
    triggers = doc.get("on", doc.get(True))
    assert "pull_request" in triggers and "pull_request_target" not in triggers
    steps = doc["jobs"]["selftest"]["steps"]
    action_steps = [s for s in steps if s.get("uses") == "./"]
    assert len(action_steps) == 2
    projects = [s["with"]["project"] for s in action_steps]
    assert projects == ["tests/fixtures/action/pass", "tests/fixtures/action/fail"]
    assert action_steps[1].get("continue-on-error") is True
    assert "continue-on-error" not in action_steps[0]
    text = _selftest_text()
    for needle in (
        '"$VERDICT" = pass',
        '"$VERDICT" = fail',
        '"$OUTCOME" = failure',
        "GITHUB_STEP_SUMMARY",
    ):
        assert needle in text
    assert doc["permissions"] == {"contents": "read"}


def test_the_fixture_projects_validate_and_hold_a_baseline_and_a_changed_source():
    for name in ("pass", "fail"):
        base = FIXTURES / name
        assert (base / "shape.yml").is_file() and (base / "data" / "orders.csv").is_file()
        assert (base / "baseline" / "orders.shape").is_file()
    assert (FIXTURES / "pass" / "baseline" / "orders.shape").read_bytes() == (
        FIXTURES / "fail" / "baseline" / "orders.shape"
    ).read_bytes()
    assert (FIXTURES / "pass" / "data" / "orders.csv").read_bytes() != (
        FIXTURES / "fail" / "data" / "orders.csv"
    ).read_bytes()


# ---- running the scripts -------------------------------------------------------------------


def _run_action(
    tmp_path: Path,
    project: str,
    *,
    fail_on: str = "fail",
    sources: str = "",
    comment: str = "true",
    event: str = "push",
    api_url: str = "http://127.0.0.1:9",
    pr: str = "",
) -> dict[str, Any]:
    runner = tmp_path / "runner"
    runner.mkdir(exist_ok=True)
    summary, output = runner / "summary.md", runner / "output.txt"
    summary.write_text("")
    output.write_text("")
    shape_dir = str(Path(sys.executable).parent)
    env = {
        **os.environ,
        "PATH": f"{shape_dir}{os.pathsep}{os.environ['PATH']}",
        "RUNNER_TEMP": str(runner),
        "GITHUB_STEP_SUMMARY": str(summary),
        "GITHUB_OUTPUT": str(output),
        "GITHUB_API_URL": api_url,
        "GITHUB_ACTIONS": "",
        "PROJECT": project,
        "SOURCES": sources,
        "FAIL_ON": fail_on,
        "POST_COMMENT": comment,
        "EVENT_NAME": event,
        "PR_NUMBER": pr,
        "REPOSITORY": "acme/data",
        "GITHUB_TOKEN": TOKEN,
    }
    check = subprocess.run(
        [BASH, "-c", _step("Check the project")["run"]],  # type: ignore[list-item]
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=300,
    )
    outputs = dict(line.split("=", 1) for line in output.read_text().splitlines() if "=" in line)
    gate_code = None
    if "verdict" in outputs:
        gate = subprocess.run(
            [BASH, "-c", _step("Gate on the result")["run"]],  # type: ignore[list-item]
            capture_output=True,
            text=True,
            env={**env, "VERDICT": outputs["verdict"]},
            cwd=tmp_path,
            timeout=60,
        )
        gate_code = gate.returncode
        assert TOKEN not in gate.stdout + gate.stderr
    assert TOKEN not in check.stdout + check.stderr + summary.read_text() + output.read_text()
    return {
        "code": check.returncode,
        "gate": gate_code,
        "outputs": outputs,
        "stdout": check.stdout,
        "stderr": check.stderr,
        "summary": summary.read_text(),
        "runner": runner,
    }


def test_the_matching_project_passes(tmp_path):
    ran = _run_action(tmp_path, str(FIXTURES / "pass"), fail_on="drift")
    assert ran["code"] == 0, ran["stderr"]
    assert ran["outputs"]["verdict"] == "pass" and ran["outputs"]["findings"] == "0"
    assert ran["gate"] == 0
    comment = Path(ran["outputs"]["comment-path"])
    assert comment.is_file() and comment.read_text().startswith("<!-- shape-pr-comment -->")
    assert "**Verdict: pass**" in ran["summary"]
    assert (ran["runner"] / "shape-action" / "orders.junit.xml").is_file()
    assert (ran["runner"] / "shape-action" / "orders.sarif").is_file()


def test_the_changed_project_fails_when_fail_on_is_drift(tmp_path):
    ran = _run_action(tmp_path, str(FIXTURES / "fail"), fail_on="drift")
    assert ran["code"] == 0, ran["stderr"]  # the check step reports; the gate step decides
    assert ran["outputs"]["verdict"] == "fail" and int(ran["outputs"]["findings"]) > 0
    assert ran["gate"] == 1
    assert "**Verdict: fail**" in ran["summary"] and "column\\_removed" in ran["summary"]


def test_with_fail_on_fail_drift_alone_does_not_fail_the_job(tmp_path):
    ran = _run_action(tmp_path, str(FIXTURES / "fail"), fail_on="fail")
    assert ran["outputs"]["verdict"] == "drift" and ran["gate"] == 0


def test_never_does_not_fail_the_job_whatever_the_verdict(tmp_path):
    ran = _run_action(tmp_path, str(FIXTURES / "fail"), fail_on="never")
    assert ran["outputs"]["verdict"] in ("drift", "fail") and ran["gate"] == 0


def test_a_command_that_cannot_run_is_a_failure_for_fail_on_fail(tmp_path):
    ran = _run_action(tmp_path, str(FIXTURES / "pass"), sources="missing-source", fail_on="fail")
    assert ran["outputs"]["verdict"] == "fail" and ran["gate"] == 1


def test_a_subset_of_the_sources_can_be_named(tmp_path):
    project = tmp_path / "two"
    shutil.copytree(FIXTURES / "pass", project)
    text = (project / "shape.yml").read_text()
    (project / "shape.yml").write_text(
        text + "  second:\n    path: data/orders.csv\n    baseline:\n      kind: pinned\n"
        "      artifact: baseline/orders.shape\n"
    )
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    both = _run_action(tmp_path / "a", str(project))
    assert both["summary"].count("### Source:") == 2
    one = _run_action(tmp_path / "b", str(project), sources="second")
    assert one["summary"].count("### Source:") == 1
    assert "Source: second" in one["summary"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"fail_on": "sometimes"}, "fail-on"),
        ({"comment": "yes"}, "comment"),
        ({"sources": "orders;touch pwned"}, "invalid source name"),
        ({"sources": "$(touch pwned)"}, "invalid source name"),
        ({"sources": "../x"}, "invalid source name"),
        ({"project": "no/such/dir"}, "project directory"),
    ],
)
def test_bad_inputs_exit_two_and_run_nothing(tmp_path, kwargs, message):
    args = {"project": str(FIXTURES / "pass"), **kwargs}
    ran = _run_action(tmp_path, args.pop("project"), **args)
    assert ran["code"] == 2 and message in ran["stdout"]
    assert not (tmp_path / "pwned").exists() and ran["outputs"] == {}


def test_a_comment_is_posted_on_a_pull_request_and_updated_on_the_next_run(tmp_path):
    with Stub() as stub:
        stub.state.author_of_new = "shape-bot"
        for round_ in (1, 2):
            work = tmp_path / f"r{round_}"
            work.mkdir()
            ran = _run_action(
                work, str(FIXTURES / "pass"), event="pull_request", api_url=stub.url, pr="12"
            )
            assert ran["code"] == 0, ran["stderr"]
        assert len(stub.state.comments) == 1
        assert stub.state.comments[0]["body"].startswith("<!-- shape-pr-comment -->")
        assert [r.method for r in stub.state.requests if r.path.startswith("/repos")] == [
            "GET",
            "POST",
            "GET",
            "PATCH",
        ]
        assert all(r.headers["Authorization"] == f"Bearer {TOKEN}" for r in stub.state.requests)


@pytest.mark.parametrize(
    ("event", "comment"), [("push", "true"), ("schedule", "true"), ("pull_request", "false")]
)
def test_no_comment_is_posted_outside_a_pull_request_or_when_turned_off(tmp_path, event, comment):
    with Stub() as stub:
        ran = _run_action(
            tmp_path, str(FIXTURES / "pass"), event=event, comment=comment, api_url=stub.url, pr="3"
        )
        assert ran["code"] == 0 and stub.state.requests == []


def test_a_comment_that_cannot_be_posted_does_not_decide_the_gate(tmp_path):
    with Stub() as stub:
        stub.state.forced.append(("POST", "/repos", 500, b"{}", {}))
        ran = _run_action(
            tmp_path, str(FIXTURES / "pass"), event="pull_request", api_url=stub.url, pr="4"
        )
        assert ran["code"] == 0 and ran["outputs"]["verdict"] == "pass" and ran["gate"] == 0
        assert "::warning::" in ran["stdout"]


def test_a_fork_pull_request_with_a_read_only_token_leaves_the_gate_to_the_result(tmp_path):
    with Stub() as stub:
        stub.state.forced.append(("POST", "/repos", 403, b"{}", {}))
        ran = _run_action(
            tmp_path,
            str(FIXTURES / "fail"),
            fail_on="drift",
            event="pull_request",
            api_url=stub.url,
            pr="4",
        )
        assert ran["code"] == 0 and ran["gate"] == 1
        assert "shape: notice:" in ran["stderr"]


def test_an_empty_or_unknown_verdict_never_passes_the_gate():
    script = _step("Gate on the result")["run"]
    for verdict in ("", "bogus"):
        for fail_on, expected in (("fail", 1), ("drift", 1), ("never", 0)):
            done = subprocess.run(
                [BASH, "-c", script],  # type: ignore[list-item]
                env={**os.environ, "VERDICT": verdict, "FAIL_ON": fail_on},
                capture_output=True,
                text=True,
            )
            assert done.returncode == expected, (verdict, fail_on)


@pytest.mark.parametrize(
    ("version", "expected"),
    [("1.0.0", "==1.0.0"), ("v1.2.3rc1", "==1.2.3rc1"), ("", "latest"), ("main", "latest")],
)
def test_the_install_step_picks_the_release(tmp_path, version, expected):
    """The install step, with `pip` replaced by a recorder."""
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "python").write_text('#!/bin/sh\necho "$@" >> "$PIPLOG"\n')
    (fake / "shape").write_text("#!/bin/sh\necho shape\n")
    for f in fake.iterdir():
        f.chmod(0o755)
    log = tmp_path / "pip.log"
    env = {
        **os.environ,
        "PATH": f"{fake}{os.pathsep}{os.environ['PATH']}",
        "PIPLOG": str(log),
        "SHAPE_VERSION": version if version != "main" else "",
        "ACTION_REF": version if version in ("main",) else "",
    }
    done = subprocess.run(
        [BASH, "-c", _step("Install Shape")["run"]], env=env, capture_output=True, text=True
    )  # type: ignore[list-item]
    assert done.returncode == 0, done.stderr
    line = log.read_text().strip()
    assert line.endswith("sqllocks-shape[yaml]" + ("" if expected == "latest" else expected))


@pytest.mark.parametrize("version", ["1.0; rm -rf /", "$(id)", "1.0 --index-url x", "latest"])
def test_a_shape_version_that_is_not_a_release_is_refused(tmp_path, version):
    done = subprocess.run(
        [BASH, "-c", _step("Install Shape")["run"]],  # type: ignore[list-item]
        env={**os.environ, "SHAPE_VERSION": version, "ACTION_REF": ""},
        capture_output=True,
        text=True,
    )
    assert done.returncode == 2 and "shape-version must be" in done.stdout


def test_installed_uses_the_shape_that_is_there(tmp_path):
    done = subprocess.run(
        [BASH, "-c", _step("Install Shape")["run"]],  # type: ignore[list-item]
        env={
            **os.environ,
            "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
            "SHAPE_VERSION": "installed",
            "ACTION_REF": "",
        },
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0 and done.stdout.startswith("shape ")
