"""Run the Fabric documentation examples against the live test workspace and record receipts.

Each page keeps its placeholders (``MyWorkspace``, ``WORKSPACE_GUID``, ``JOB`` ...). The manifest
rewrites a placeholder to a shell reference to a real test value (``"$FABRIC_WORKSPACE_ID"``), so a
value never appears in a file; the output is scrubbed back to the placeholder before it is stored.
A note is resolved only by an exit code 0 and an output without a credential. A failure keeps the
note, the page and its expected output untouched, and its receipt records the exact error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

BLOCK = re.compile(
    r"<!-- example: (?P<index>\d+) -->\n\n"
    r"(?P<intro>\*\*Needs a Fabric account\. Not run in CI\.\*\*)"
    r"\n\n```(?P<lang>[a-z]*)\n(?P<command>.*?)\n```\n\n<!-- owner: (?P<note>[^\n]*?) -->",
    re.S,
)
# Values that identify the test tenant or workspace but are not credentials: stored as placeholders.
PLACEHOLDERS = {
    "FABRIC_TENANT_ID": "<tenant-id>",
    "FABRIC_CLIENT_ID": "<client-id>",
    "FABRIC_WORKSPACE_ID": "<workspace-id>",
    "FABRIC_LAKEHOUSE_ID": "<lakehouse-id>",
    "FABRIC_GIT_WORKSPACE_ID": "<git-workspace-id>",
    "FABRIC_SQL_ENDPOINT": "<sql-endpoint>",
    "FABRIC_EVENTHOUSE_URI": "<eventhouse-uri>",
    "FABRIC_SQL_CONNECTION_STRING": "<sql-database-connection-string>",
    "FABRIC_SAMPLE_WAREHOUSE_CONNECTION_STRING": "<sample-warehouse-connection-string>",
    "FABRIC_WAREHOUSE_CONNECTION_STRING": "<warehouse-connection-string>",
    "SHAPE_TEST_SYNAPSE_URI": "synapse://<workspace>.sql.azuresynapse.net/<pool>",
    "SHAPE_TEST_SYNAPSE_STAGING": "abfss://<container>@<account>.dfs.core.windows.net/<folder>",
    "DOCS_SAMPLE_SERVER": "<sql-endpoint>",
    "DOCS_KQL_HOST": "<query-uri-host>",
    "DOCS_JOB_ID": "<job-id>",
    "DOCS_WAREHOUSE_CONNECTION": "<warehouse-connection-string>",
    "DOCS_SAMPLE_DB": "<database>",
    "DOCS_WS": "<workspace-id>",
    "DOCS_LH": "<lakehouse-id>",
    "DOCS_SEM_TABLE": "<table>",
}
GUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
HOSTS = (
    (r"[A-Za-z0-9.-]+\.datawarehouse\.fabric\.microsoft\.com", "<sql-endpoint>"),
    (r"[A-Za-z0-9.-]+\.database\.fabric\.microsoft\.com", "<sql-endpoint>"),
    (r"[A-Za-z0-9.-]+\.kusto\.fabric\.microsoft\.com", "<query-uri-host>"),
    (r"[A-Za-z0-9.-]+\.sql\.azuresynapse\.net", "<workspace>.sql.azuresynapse.net"),
)
CREDENTIAL_WORDS = ("PASSWORD", "SECRET", "TOKEN", "KEY", "CONNECTION_STRING", "SAS")


def rewrite(command: str, rules: list[list[str]]) -> str:
    """Apply the manifest's placeholder-to-shell-reference rules to a page command."""
    for pattern, replacement in rules:
        command = re.sub(pattern, replacement, command)
    return command


def credentials(env: dict[str, str]) -> dict[str, str]:
    """Values of the environment that must never reach a receipt or a page."""
    return {
        key: value
        for key, value in env.items()
        if key not in PLACEHOLDERS
        and any(word in key.upper() for word in CREDENTIAL_WORDS)
        and len(value) >= 8
        and not key.startswith(("GITHUB_", "RUNNER_", "ACTIONS_", "INPUT_", "SHAPE_DOCS_"))
    }


def scrub(
    text: str, env: dict[str, str], root: Path, work: Path, names: dict[str, str] | None = None
) -> str:
    """Replace the test tenant's identifiers by the placeholders the pages use."""
    for real, placeholder in (names or {}).items():
        text = text.replace(real, placeholder)
    for key in sorted(PLACEHOLDERS, key=lambda k: -len(env.get(k, ""))):
        value = env.get(key, "")
        if len(value) >= 4:
            text = text.replace(value, PLACEHOLDERS[key])
    for pattern, replacement in HOSTS:
        text = re.sub(pattern, replacement, text)
    text = GUID.sub("<guid>", text)
    text = text.replace(str(work), "<work>").replace(str(root), "<checkout>")
    return text


def leaked(output: str, env: dict[str, str]) -> list[str]:
    """Names of credential variables whose value appears in ``output``."""
    return sorted(key for key, value in credentials(env).items() if value in output)


def resolve_job_id(output: str, jobs_dir: Path) -> str:
    """The job a fabric_spark run created: from its output, else the newest stored record."""
    found = re.search(r"\bjob-[0-9a-f]{12}\b", output)
    if found:
        return found.group(0)
    records = sorted(jobs_dir.glob("job-*.json"), key=lambda p: p.stat().st_mtime)
    return records[-1].stem if records else ""


def machine() -> dict[str, Any]:
    """The runner's actual details (reused from the owner transcripts)."""
    from docs_owner_transcripts import machine as owner_machine

    return owner_machine()


def resolved_text(
    text: str, match: re.Match[str], shown: str, evidence: str, after: str = ""
) -> str:
    """The page with the example's note replaced by its recorded output and evidence link."""
    lang = match["lang"]
    block = (
        f"<!-- example: {match['index']} -->\n\n{match['intro']}\n\n"
        "Recorded by a manual `workflow_dispatch` run against the Fabric test workspace; the "
        "angle-bracket values stand for that tenant's identifiers."
        + (f" {after}" if after else "")
        + "\n\n"
        f"```{lang}\n{match['command']}\n```\n\n"
        '??? info "Output (exit 0)"\n\n    ```text {.expected}\n'
        + "\n".join("    " + line for line in shown.splitlines())
        + f"\n    ```\n\n{evidence}"
    )
    return text[: match.start()] + block + text[match.end() :]


def capture(
    root: Path,
    manifest: list[dict[str, Any]],
    *,
    run_id: str,
    commit: str,
    runner: str,
    only: set[str] | None = None,
) -> int:
    """Run each example, write its receipt, and resolve its note only on a clean exit 0."""
    destination = root / "docs/plans/evidence/owner-notes"
    destination.mkdir(parents=True, exist_ok=True)
    host = machine()
    home = root / ".fabric-transcript-work"
    failed = False
    outputs: dict[str, str] = {}
    for item in manifest:
        if only and item["id"] not in only:
            continue
        page = root / item["page"]
        text = page.read_text()
        match = next(
            (m for m in BLOCK.finditer(text) if m["index"] == str(item["index"])),
            None,
        )
        if match is None:
            raise ValueError(f"No unresolved example {item['index']} in {item['page']}")
        work = home / item["id"]
        work.mkdir(parents=True, exist_ok=True)
        env = {
            **os.environ,
            "SHAPE_DOCS_REPO": str(root),
            "SHAPE_HOME": str(home / ".shape"),
            "SHAPE_JOBS_DIR": str(home / "jobs"),
            "NO_COLOR": "1",
            "SHAPE_CONFIRM_REMOTE": "1",
            "COLUMNS": "100",
            "PIP_QUIET": "2",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        }
        for name, source in item.get("env", {}).items():
            env[name] = env.get(source, "") if isinstance(source, str) else ""
        if "job_from" in item:
            env["DOCS_JOB_ID"] = resolve_job_id(outputs.get(item["job_from"], ""), home / "jobs")
        command = rewrite(match["command"], item.get("rewrite", []))
        missing = [n for n in item.get("needs", []) if not env.get(n)]
        started = time.perf_counter()
        if missing:
            output, code = "missing value: " + ", ".join(missing) + "\n", 125
        else:
            try:
                run = subprocess.run(
                    ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", command],
                    cwd=root if item.get("cwd") == "repo" else work,
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=item.get("timeout", 1200),
                )
                output, code = run.stdout, run.returncode
            except subprocess.TimeoutExpired as exc:
                partial = exc.stdout
                output = partial.decode() if isinstance(partial, bytes) else (partial or "")
                output += "\nCommand exceeded its time limit; no successful transcript.\n"
                code = 124
        outputs[item["id"]] = output
        status = "passed" if code == 0 else "failed"
        names = leaked(output, env)
        if names:
            status = "credential-leak"
            for key in names:
                output = output.replace(env[key], "<redacted>")
        shown = scrub(output, env, root, work, item.get("scrub_names"))
        stem = f"docs-transcripts-{run_id}-{commit[:12]}-{runner}-{item['id']}"
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", stem):
            raise ValueError("Run, commit and runner must be safe filename components")
        receipt = destination / (stem + ".json")
        receipt.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "commit": commit,
                    "runner": runner,
                    "machine": host,
                    "workspace": "shape-test, service principal shape-ci-fabric",
                    "page": item["page"],
                    "example": item["id"],
                    "command": match["command"],
                    "executed_command": scrub(command, env, root, work, item.get("scrub_names")),
                    "exit_code": code,
                    "status": status,
                    "elapsed_seconds": time.perf_counter() - started,
                    "stdout": shown,
                },
                indent=2,
            )
            + "\n"
        )
        print(f"{item['id']}: {status}, exit {code}; {receipt.relative_to(root)}", flush=True)
        if status != "passed":
            failed = True
            continue
        public = root / "docs/evidence/transcripts" / receipt.name
        public.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(receipt, public)
        target = Path(os.path.relpath(public, page.parent)).as_posix()
        evidence = f"Evidence: [run {run_id}, {runner}, `{commit[:12]}`]({target})."
        page.write_text(
            resolved_text(
                text,
                match,
                shown.rstrip("\n") or "(no output)",
                evidence,
                item.get("after_text", ""),
            )
        )
    return int(failed)


def main() -> int:
    """Capture the checked-in Fabric inventory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "tests/docs/fabric_examples.json")
    parser.add_argument("--only", default="", help="comma-separated example ids (default: all)")
    args = parser.parse_args()
    only = {i for i in args.only.split(",") if i} or None
    return capture(
        ROOT,
        json.loads(args.manifest.read_text()),
        run_id=os.environ["GITHUB_RUN_ID"],
        commit=os.environ["GITHUB_SHA"],
        runner=os.environ.get("SHAPE_DOCS_RUNNER", "ubuntu-latest"),
        only=only,
    )


if __name__ == "__main__":
    raise SystemExit(main())
