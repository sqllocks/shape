"""Owner receipts are captured from commands and never turn failures into evidence."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def page(root: Path, command: str, expected: str = "") -> Path:
    path = root / "docs/demo.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "<!-- owner-example: demo -->\n\n```bash {.runnable-owner}\n"
        + command
        + "\n```\n\n<!-- owner: maintainer — capture demo. -->\n"
        + expected
    )
    return path


def test_capture_success_names_run_commit_runner_and_resolves_only_matching_note(tmp_path):
    from docs_owner_transcripts import capture

    path = page(tmp_path, "printf 'real output\\n'")
    manifest = [{"id": "demo", "page": "docs/demo.md", "note": "maintainer — capture demo."}]
    result = capture(tmp_path, manifest, run_id="123", commit="a" * 40, runner="ubuntu-latest")
    assert result == 0
    receipts = list((tmp_path / "docs/plans/evidence/owner-notes").glob("*.json"))
    assert len(receipts) == 1
    record = json.loads(receipts[0].read_text())
    assert (record["run_id"], record["commit"], record["runner"]) == (
        "123",
        "a" * 40,
        "ubuntu-latest",
    )
    assert record["stdout"] == "real output\n"
    assert record["exit_code"] == 0
    assert {"cpu", "ram_bytes", "os", "python", "pyarrow", "kernel"} <= record["machine"].keys()
    assert "<!-- owner:" not in path.read_text()
    assert "    real output" in path.read_text()
    assert receipts[0].name.startswith("docs-transcripts-123-aaaaaaaaaaaa-ubuntu-latest-demo")


@pytest.mark.parametrize("command", ["printf 'failure\\n'; exit 2", "false; printf 'masked\\n'"])
def test_failed_command_keeps_owner_and_expected_output(tmp_path, command):
    from docs_owner_transcripts import capture

    path = page(tmp_path, command)
    before = path.read_text()
    assert (
        capture(
            tmp_path,
            [{"id": "demo", "page": "docs/demo.md", "note": "maintainer — capture demo."}],
            run_id="1",
            commit="b" * 40,
            runner="test",
        )
        == 1
    )
    assert path.read_text() == before
    record = json.loads(
        next((tmp_path / "docs/plans/evidence/owner-notes").glob("*.json")).read_text()
    )
    assert record["exit_code"] != 0
    assert record["status"] == "failed"


def test_existing_expected_output_is_not_rewritten_on_mismatch(tmp_path):
    from docs_owner_transcripts import capture

    expected = '\n??? info "Output (exit 0)"\n\n    ```text {.expected}\n    expected\n    ```\n'
    path = page(tmp_path, "printf 'different\\n'", expected)
    before = path.read_text()
    assert (
        capture(
            tmp_path,
            [{"id": "demo", "page": "docs/demo.md", "note": "maintainer — capture demo."}],
            run_id="1",
            commit="b" * 40,
            runner="test",
        )
        == 1
    )
    assert path.read_text() == before


def test_credentials_are_redacted_and_cannot_resolve_a_note(tmp_path, monkeypatch):
    from docs_owner_transcripts import capture

    monkeypatch.setenv("REGISTRY_PASSWORD", "private-test-value")
    path = page(tmp_path, 'printf "%s\\n" "$REGISTRY_PASSWORD"')
    before = path.read_text()
    assert (
        capture(
            tmp_path,
            [{"id": "demo", "page": "docs/demo.md", "note": "maintainer — capture demo."}],
            run_id="1",
            commit="b" * 40,
            runner="test",
        )
        == 1
    )
    assert path.read_text() == before
    evidence = next((tmp_path / "docs/plans/evidence/owner-notes").glob("*.json")).read_text()
    assert "private-test-value" not in evidence
    assert "credential-leak" in evidence


def test_workflow_has_services_write_permissions_and_branch_guard():
    workflow = yaml.safe_load((ROOT / ".github/workflows/docs-transcripts.yml").read_text())
    triggers = workflow.get("on", workflow.get(True))
    assert "workflow_dispatch" in triggers
    assert triggers["push"]["branches"] == ["docs/transcripts-ci"]
    job = workflow["jobs"]["capture"]
    assert workflow["permissions"]["contents"] == "write"
    assert {"postgres", "kafka", "schema-registry", "mssql"} <= job["services"].keys()
    assert "github.ref == 'refs/heads/docs/transcripts-ci'" in job["if"]
    assert any(
        step.get("uses", "").startswith("actions/upload-artifact@") and step.get("if") == "always()"
        for step in job["steps"]
    )
    assert not any(step.get("continue-on-error") for step in job["steps"])
    assert (ROOT / "THIRD_PARTY_NOTICES.md").read_bytes() == (
        ROOT / "plugins/shape-domains/THIRD_PARTY_NOTICES.md"
    ).read_bytes()


def test_timeout_is_a_failed_receipt_and_keeps_note(tmp_path):
    from docs_owner_transcripts import capture

    path = page(tmp_path, "sleep 1")
    before = path.read_text()
    manifest = [
        {
            "id": "demo",
            "page": "docs/demo.md",
            "note": "maintainer — capture demo.",
            "timeout": 0.01,
        }
    ]
    assert capture(tmp_path, manifest, run_id="1", commit="b" * 40, runner="test") == 1
    assert path.read_text() == before
    receipt = next((tmp_path / "docs/plans/evidence/owner-notes").glob("*.json"))
    assert json.loads(receipt.read_text())["exit_code"] == 124


def test_inventory_captures_the_page_commands_without_substitution():
    from docs_owner_transcripts import BLOCK

    items = json.loads((ROOT / "tests/docs/owner_examples.json").read_text())
    assert len(items) == 11
    assert len({item["id"] for item in items}) == len(items)
    for item in items:
        text = (ROOT / item["page"]).read_text()
        blocks = [block for block in BLOCK.finditer(text) if block[1] == item["id"]]
        assert len(blocks) == 1, item
        assert blocks[0][2].strip(), item
    workflow = yaml.safe_load((ROOT / ".github/workflows/docs-transcripts.yml").read_text())
    steps = workflow["jobs"]["capture"]["steps"]
    assert steps[-1]["run"] == 'test "${CAPTURE_EXIT:-1}" = 0'
    assert any("git push origin HEAD:docs/transcripts-ci" in step.get("run", "") for step in steps)


def test_drift_fixture_applies_to_the_actual_retail_schema(tmp_path):
    import subprocess

    from shape.generation.domains import load_domain
    from shape.generation.drift_plan import DriftPlan

    subprocess.run(
        [sys.executable, str(ROOT / "scripts/docs_owner_setup.py"), "drift"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    plan = DriftPlan.load(tmp_path / "plan.json")
    schema = load_domain("retail").schema
    before = plan.schema_at(schema, 0)
    after = plan.schema_at(schema, 1)
    assert before.tables["order_line"].columns["quantity"].generator["params"]["p"] == 0.6
    assert after.tables["order_line"].columns["quantity"].generator["params"]["p"] == 0.48
