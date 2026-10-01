"""Shape quality gate for an Azure Data Factory Batch Custom activity (PF-04).

Runs inside the Shape container image (PF-05) on an Azure Batch node. It calls the Shape CLI:

    shape profile SOURCE -o profile.shape --json summary.json
    shape check   profile.shape contract.json --json check.json      (when a contract is given)
    shape diff    baseline.shape profile.shape --json diff.json      (when a baseline is given)

then writes ``gate.json``, ``profile.shape`` and ``summary.json`` under ``outputUrl`` (an
``abfss://`` URL, or a local path when testing) and exits with the gate's exit code, so the
Custom activity fails exactly when the gate does:

    0  the profile was written and every check passed
    1  a contract violation, or drift against the baseline with ``failOnDrift``
    2  an error: unreadable source, bad contract, unreachable storage (``gate.json`` says what)

``gate.json`` is compact (lists capped at 100 entries) so a Lookup activity can read it back
(its limit is 4 MB) and branch on ``passed``::

    {"passed": bool, "exitCode": int, "rowCount": int|null, "violations": [...],
     "drifted": bool, "changes": [...], "artifactUrl": str|null, "error": str|null,
     "truncated": bool}

Settings come from the Custom activity's ``extendedProperties`` (ADF writes them to
``activity.json`` in the task's working folder): ``sourceUrl``, ``outputUrl`` and the optional
``contractUrl``, ``baselineUrl``, ``failOnDrift``, ``managedIdentityClientId``. Storage is
reached with the pool's managed identity through ``DefaultAzureCredential``; for a user-assigned
identity ``managedIdentityClientId`` is exported as ``AZURE_CLIENT_ID``.

Only the standard library, ``fsspec`` and (for ``abfss://``) ``adlfs`` and ``azure-identity`` are
used; all are in the container image.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

MAX_LISTED = 100
GATE_FILE = "gate.json"
PROFILE_FILE = "profile.shape"
SUMMARY_FILE = "summary.json"
_STDERR_TAIL = 800
_ABFS = re.compile(r"^abfss?://", re.IGNORECASE)


class GateError(Exception):
    """The gate could not be evaluated (exit code 2)."""


def load_settings(path: str | Path) -> dict[str, Any]:
    """The settings dict from ADF's ``activity.json`` (``typeProperties.extendedProperties``),
    or from a plain JSON object with the same keys."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GateError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise GateError(f"{path} is not a JSON object")
    ext = (
        data.get("typeProperties", {}).get("extendedProperties")
        if "typeProperties" in data
        else data
    )
    if not isinstance(ext, dict):
        raise GateError(f"{path} has no extendedProperties object")
    return ext


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y")
    return bool(value)


# -------------------------------------------------------------------------------- storage


def _filesystem(url: str, client_id: str) -> tuple[Any, str]:
    """(fsspec filesystem, path) for ``url``: ``abfss://`` through adlfs with the managed
    identity; anything else is a local path."""
    import fsspec

    if _ABFS.match(url):
        parsed = urlparse(url)
        container = parsed.netloc.partition("@")[0] if "@" in parsed.netloc else ""
        account = (parsed.hostname or "").split(".")[0]
        if not container or not account:
            raise GateError(f"not an abfss://<container>@<account>.dfs.core.windows.net URL: {url}")
        try:
            from azure.identity import DefaultAzureCredential
        except ImportError as exc:  # pragma: no cover - the image has it
            raise GateError("azure-identity is not installed") from exc
        credential = (
            DefaultAzureCredential(managed_identity_client_id=client_id)
            if client_id
            else DefaultAzureCredential()
        )
        fs = fsspec.filesystem("abfss", account_name=account, credential=credential)
        return fs, f"{container}{parsed.path}"
    return fsspec.filesystem("file"), url


def _download(url: str, dest: Path, client_id: str) -> None:
    fs, path = _filesystem(url, client_id)
    try:
        with fs.open(path, "rb") as src, open(dest, "wb") as out:
            out.write(src.read())
    except FileNotFoundError as exc:
        raise GateError(f"{url} does not exist") from exc
    except Exception as exc:
        raise GateError(f"cannot read {url}: {type(exc).__name__}: {exc}") from exc


def _upload(src: Path, base_url: str, name: str, client_id: str) -> str:
    target = base_url.rstrip("/") + "/" + name
    fs, path = _filesystem(target, client_id)
    try:
        parent = path.rsplit("/", 1)[0]
        fs.makedirs(parent, exist_ok=True)
        with open(src, "rb") as data, fs.open(path, "wb") as out:
            out.write(data.read())
    except Exception as exc:
        raise GateError(f"cannot write {target}: {type(exc).__name__}: {exc}") from exc
    return target


# ------------------------------------------------------------------------------- the gate


def _shape(
    cmd: list[str], args: list[str], env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run([*cmd, *args], capture_output=True, text=True, env=env, check=False)


def _tail(proc: subprocess.CompletedProcess[str]) -> str:
    text = (proc.stderr or proc.stdout or "").strip()
    return text[-_STDERR_TAIL:]


def _new_gate(error: str | None = None) -> dict[str, Any]:
    """The gate document: an error gate (exit code 2) until the checks have run."""
    return {
        "passed": False,
        "exitCode": 2,
        "rowCount": None,
        "violations": [],
        "drifted": False,
        "changes": [],
        "artifactUrl": None,
        "error": error,
        "truncated": False,
    }


def evaluate(settings: dict[str, Any], work: Path, shape_cmd: list[str]) -> dict[str, Any]:
    """Run profile, check and diff in ``work``; return the gate document (without uploading)."""
    source = str(settings.get("sourceUrl") or "")
    if not source:
        raise GateError("sourceUrl is required")
    client_id = str(settings.get("managedIdentityClientId") or "")
    env = dict(os.environ)
    if client_id:
        env["AZURE_CLIENT_ID"] = client_id  # DefaultAzureCredential picks a user-assigned identity
    gate = _new_gate()
    profile, summary = work / PROFILE_FILE, work / SUMMARY_FILE
    proc = _shape(shape_cmd, ["profile", source, "-o", str(profile), "--json", str(summary)], env)
    if proc.returncode != 0:
        raise GateError(f"shape profile exited {proc.returncode}: {_tail(proc)}")
    gate["rowCount"] = json.loads(summary.read_text(encoding="utf-8")).get("row_count")

    violations: list[dict[str, Any]] = []
    contract = str(settings.get("contractUrl") or "")
    if contract:
        contract_file, check_file = work / "contract.json", work / "check.json"
        _download(contract, contract_file, client_id)
        proc = _shape(
            shape_cmd,
            ["check", str(profile), str(contract_file), "--json", str(check_file)],
            env,
        )
        if proc.returncode not in (0, 1):
            raise GateError(f"shape check exited {proc.returncode}: {_tail(proc)}")
        result = json.loads(check_file.read_text(encoding="utf-8"))
        violations = list(result.get("violations", []))
        if proc.returncode == 1 and not violations:
            raise GateError("shape check exited 1 without violations")

    changes: list[dict[str, Any]] = []
    baseline = str(settings.get("baselineUrl") or "")
    if baseline:
        baseline_file, diff_file = work / "baseline.shape", work / "diff.json"
        _download(baseline, baseline_file, client_id)
        proc = _shape(
            shape_cmd,
            ["diff", str(baseline_file), str(profile), "--json", str(diff_file)],
            env,
        )
        if proc.returncode != 0:
            raise GateError(f"shape diff exited {proc.returncode}: {_tail(proc)}")
        diff = json.loads(diff_file.read_text(encoding="utf-8"))
        gate["drifted"] = bool(diff.get("drifted"))
        changes = list(diff.get("changes", []))
        if gate["drifted"] and _as_bool(settings.get("failOnDrift", False)):
            violations.append(
                {
                    "column": "*",
                    "rule": "drift",
                    "expected": "no drift against baseline",
                    "observed": f"{len(changes)} change(s)",
                }
            )

    gate["passed"] = not violations
    gate["exitCode"] = 0 if gate["passed"] else 1
    gate["truncated"] = len(violations) > MAX_LISTED or len(changes) > MAX_LISTED
    gate["violations"] = violations[:MAX_LISTED]
    gate["changes"] = changes[:MAX_LISTED]
    return gate


def run(settings: dict[str, Any], shape_cmd: list[str], workdir: str | Path | None = None) -> int:
    """Evaluate the gate, publish its files, return the exit code."""
    output = str(settings.get("outputUrl") or "")
    if not output:
        raise GateError("outputUrl is required")
    client_id = str(settings.get("managedIdentityClientId") or "")
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        work = Path(tmp)
        try:
            gate = evaluate(settings, work, shape_cmd)
            gate["artifactUrl"] = _upload(work / PROFILE_FILE, output, PROFILE_FILE, client_id)
            _upload(work / SUMMARY_FILE, output, SUMMARY_FILE, client_id)
        except GateError as exc:
            gate = _new_gate(str(exc))
        gate_file = work / GATE_FILE
        gate_file.write_text(json.dumps(gate), encoding="utf-8")
        _upload(gate_file, output, GATE_FILE, client_id)  # a failed gate still reports why
        print(json.dumps({k: gate[k] for k in ("passed", "exitCode", "rowCount", "error")}))
        return int(gate["exitCode"])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    p.add_argument(
        "--activity",
        default="activity.json",
        help="ADF's activity.json (or a JSON object with the settings); default: ./activity.json",
    )
    p.add_argument(
        "--shape",
        default="shape",
        help="the Shape CLI command (default: shape); a command line, split on spaces",
    )
    a = p.parse_args(argv)
    try:
        settings = load_settings(a.activity)
        return run(settings, a.shape.split())
    except GateError as exc:
        print(f"shape gate: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
