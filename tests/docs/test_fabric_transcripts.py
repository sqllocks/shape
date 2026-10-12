"""Fabric transcripts: real values exist only during the run, and failures stay failures."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

NOTE = "Fabric maintainer — supply the transcript for docs/demo.md example 0."


def page(root: Path, command: str) -> Path:
    path = root / "docs/demo.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "<!-- example: 0 -->\n\n**Needs a Fabric account. Not run in CI.**\n\n```bash\n"
        + command
        + f"\n```\n\n<!-- owner: {NOTE} -->\n\nafter\n"
    )
    return path


def item(**extra):
    return {"id": "demo", "page": "docs/demo.md", "index": 0, "note": NOTE, **extra}


def run(root: Path, manifest, **kw):
    from docs_fabric_transcripts import capture

    return capture(root, manifest, run_id="9", commit="c" * 40, runner="test", **kw)


def test_success_runs_substituted_command_and_stores_only_placeholders(tmp_path, monkeypatch):
    monkeypatch.setenv("FABRIC_WORKSPACE_ID", "11111111-2222-3333-4444-555555555555")
    path = page(tmp_path, "echo workspace WORKSPACE_GUID")
    manifest = [item(rewrite=[["WORKSPACE_GUID", '"$FABRIC_WORKSPACE_ID"']])]
    assert run(tmp_path, manifest) == 0
    text = path.read_text()
    assert "<!-- owner:" not in text
    assert "    workspace <workspace-id>" in text
    assert "echo workspace WORKSPACE_GUID" in text
    assert "11111111-2222-3333-4444-555555555555" not in text
    receipt = next((tmp_path / "docs/plans/evidence/owner-notes").glob("*.json")).read_text()
    assert "11111111-2222-3333-4444-555555555555" not in receipt
    assert json.loads(receipt)["exit_code"] == 0
    assert (tmp_path / "docs/evidence/transcripts").is_dir()
    assert "Evidence: [run 9, test, `cccccccccccc`]" in text


def test_failure_keeps_the_note_and_records_the_error(tmp_path):
    path = page(tmp_path, "echo 'HTTP 403 Forbidden'; exit 1")
    before = path.read_text()
    assert run(tmp_path, [item()]) == 1
    assert path.read_text() == before
    record = json.loads(
        next((tmp_path / "docs/plans/evidence/owner-notes").glob("*.json")).read_text()
    )
    assert (record["status"], record["exit_code"]) == ("failed", 1)
    assert "HTTP 403 Forbidden" in record["stdout"]


def test_missing_value_fails_naming_it_and_runs_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("DOCS_NOT_SET", raising=False)
    path = page(tmp_path, "touch ran")
    before = path.read_text()
    assert run(tmp_path, [item(needs=["DOCS_NOT_SET"])]) == 1
    assert path.read_text() == before
    assert not (tmp_path / ".fabric-transcript-work/demo/ran").exists()
    assert (
        "DOCS_NOT_SET"
        in next((tmp_path / "docs/plans/evidence/owner-notes").glob("*.json")).read_text()
    )


def test_credential_in_output_cannot_resolve_a_note(tmp_path, monkeypatch):
    monkeypatch.setenv("FABRIC_CLIENT_SECRET", "super-private-value")
    path = page(tmp_path, 'echo "$FABRIC_CLIENT_SECRET"')
    before = path.read_text()
    assert run(tmp_path, [item()]) == 1
    assert path.read_text() == before
    receipt = next((tmp_path / "docs/plans/evidence/owner-notes").glob("*.json")).read_text()
    assert "super-private-value" not in receipt
    assert "credential-leak" in receipt


def test_remaining_guids_and_hosts_are_scrubbed(tmp_path):
    path = page(
        tmp_path, "echo 7a6b5c4d-1111-2222-3333-444455556666 abc.datawarehouse.fabric.microsoft.com"
    )
    assert run(tmp_path, [item()]) == 0
    assert "<guid> <sql-endpoint>" in path.read_text()


def test_job_id_comes_from_the_earlier_example_or_the_newest_record(tmp_path):
    from docs_fabric_transcripts import resolve_job_id

    assert resolve_job_id("started job-0123456789ab", tmp_path) == "job-0123456789ab"
    (tmp_path / "job-aaaaaaaaaaaa.json").write_text("{}")
    assert resolve_job_id("no id here", tmp_path) == "job-aaaaaaaaaaaa"
    assert resolve_job_id("", tmp_path / "missing") == ""


def test_manifest_matches_the_unresolved_fabric_notes():
    from docs_fabric_transcripts import BLOCK

    items = json.loads((ROOT / "tests/docs/fabric_examples.json").read_text())
    assert len({i["id"] for i in items}) == len(items)
    for entry in items:
        text = (ROOT / entry["page"]).read_text()
        found = [m for m in BLOCK.finditer(text) if m["index"] == str(entry["index"])]
        resolved = "Evidence: [run" in text and entry["note"] not in text
        assert found or resolved, entry["id"]
        if found:
            assert found[0]["note"] == entry["note"]
            for pattern, _ in entry.get("rewrite", []):
                import re

                assert re.search(pattern, found[0]["command"]), (entry["id"], pattern)


def test_workflow_shares_the_fabric_live_group_and_is_manual_only():
    workflow = yaml.safe_load((ROOT / ".github/workflows/docs-fabric-transcripts.yml").read_text())
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers) == {"workflow_dispatch"}
    assert workflow["concurrency"] == {"group": "fabric-live", "cancel-in-progress": False}
    job = workflow["jobs"]["capture"]
    assert job["environment"] == "fabric-live"
    assert job["timeout-minutes"] <= 150
    assert job["if"] == "github.ref == 'refs/heads/docs/fabric-transcripts'"
    assert not any(step.get("continue-on-error") for step in job["steps"])
    assert any(s.get("uses", "").startswith("actions/upload-artifact@") for s in job["steps"])
