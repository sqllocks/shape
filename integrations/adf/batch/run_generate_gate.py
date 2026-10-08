"""Shape generate-then-check gate for an Azure Data Factory Batch Custom activity (PF-06).

Runs inside the Shape container image (PF-05) on an Azure Batch node, next to ``run_gate.py`` (it
reuses that script's storage and settings code). It calls the Shape CLI:

    shape generate DOMAIN --scale S --seed N [--mode M] --format parquet -o data/
    shape check    profile.shape contract.json --json check.json
    shape diff     baseline.shape profile.shape --json diff.json      (when a baseline is given)

and profiles the generated tables in process, together as a dataset (``shape profile --dataset
data/`` does the same from the command line; without ``--dataset`` a folder is one table).

``contract.json`` is the contract the domain's own schema implies for the tables just generated
(``shape.integrations.fabric.generation.contract_for_domain``: exact row counts, columns, types,
nullability, primary keys, enumerated values). Then it writes under ``outputUrl`` (an ``abfss://``
URL, or a local path when testing) ``data/<table>.parquet``, ``contract.json``, ``profile.shape``,
``summary.json`` and ``gate.json``, and exits with the gate's exit code, so the Custom activity
fails exactly when the gate does:

    0  the data was generated and every check passed
    1  the generated data broke the contract, or drifted from the baseline with ``failOnDrift``
    2  an error: unknown domain, bad settings, a failed command, unreachable storage

``gate.json`` has the keys ``run_gate.py`` writes, plus ``domain``, ``tables`` (rows per table)
and ``contractUrl``; the Lookup activity reads it back and branches on ``passed``.

Settings come from the Custom activity's ``extendedProperties`` (ADF writes them to
``activity.json``): ``domain``, ``outputUrl``, and the optional ``scale`` (default ``small``),
``seed`` (default 42), ``mode``, ``baselineUrl``, ``failOnDrift``, ``maxRows`` (refuse a larger
scale, default 50,000,000) and ``managedIdentityClientId``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_gate  # noqa: E402
from run_gate import GateError  # noqa: E402

DATA_DIR = "data"
CONTRACT_FILE = "contract.json"
DEFAULT_MAX_ROWS = 50_000_000


def _generation() -> Any:
    try:
        from shape.integrations.fabric import generation
    except ImportError as exc:  # pragma: no cover - the image has it
        raise GateError(f"the Shape package is not importable: {exc}") from exc
    return generation


def _setting(settings: dict[str, Any], key: str, default: Any) -> Any:
    value = settings.get(key)
    return default if value in (None, "") else value


def _row_counts(data_dir: Path) -> dict[str, int]:
    import pyarrow.parquet as pq

    counts = {
        p.stem: int(pq.ParquetFile(p).metadata.num_rows) for p in sorted(data_dir.glob("*.parquet"))
    }
    if not counts:
        raise GateError("shape generate wrote no Parquet files")
    return counts


def evaluate(settings: dict[str, Any], work: Path, shape_cmd: list[str]) -> dict[str, Any]:
    """Generate, profile and check in ``work``; return the gate document (without uploading)."""
    generation = _generation()
    try:
        domain = generation.check_name(str(settings.get("domain") or ""), "domain")
        scale = generation.check_name(str(_setting(settings, "scale", "small")), "scale")
        mode = str(_setting(settings, "mode", "")) or None
        seed = int(_setting(settings, "seed", 42))
        max_rows = int(_setting(settings, "maxRows", DEFAULT_MAX_ROWS))
        planned = generation.plan_row_counts(domain, scale, mode)
    except (generation.GenerationRequestError, ValueError) as exc:
        raise GateError(str(exc)) from exc
    if sum(planned.values()) > max_rows:
        raise GateError(
            f"{domain!r} at scale {scale!r} is {sum(planned.values()):,} rows, above "
            f"maxRows={max_rows:,}"
        )

    client_id = str(settings.get("managedIdentityClientId") or "")
    env = dict(os.environ)
    if client_id:
        env["AZURE_CLIENT_ID"] = client_id  # DefaultAzureCredential picks a user-assigned identity
    gate = run_gate._new_gate()
    gate["domain"] = domain
    gate["tables"] = {}
    data_dir = work / DATA_DIR
    profile, summary = work / run_gate.PROFILE_FILE, work / run_gate.SUMMARY_FILE

    args = ["generate", domain, "--scale", scale, "--seed", str(seed)]
    if mode:
        args += ["--mode", mode]
    proc = run_gate._shape(shape_cmd, [*args, "--format", "parquet", "-o", str(data_dir)], env)
    if proc.returncode != 0:
        raise GateError(f"shape generate exited {proc.returncode}: {run_gate._tail(proc)}")
    counts = _row_counts(data_dir)
    gate["tables"] = counts
    gate["rowCount"] = sum(counts.values())

    # The contract expects the rows the schema and scale plan, not the rows that were written:
    # a table that came out short must fail it.
    contract_file = work / CONTRACT_FILE
    try:
        contract = generation.contract_for_domain(domain, planned, mode)
    except generation.GenerationRequestError as exc:
        raise GateError(str(exc)) from exc
    contract_file.write_text(json.dumps(contract), encoding="utf-8")

    # The tables are profiled together in process (a dict of tables: keys and relationships between
    # them are detected), and saved as a dataset profile, which is what a multi-table contract is
    # checked against. The guard below stays although `shape check` now refuses a mismatch itself.
    profile_tables = generation.profile_tables(data_dir, domain)
    if not profile_tables.is_dataset or set(profile_tables.tables) != set(counts):
        raise GateError("the profile does not hold the generated tables")  # never a vacuous check
    try:
        import shape

        shape.save(profile_tables, str(profile), capture=run_gate.GATE_CAPTURE)
        summary.write_text(json.dumps(profile_tables.summary()), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - any failure here is an error gate
        raise GateError(f"cannot write the profile: {type(exc).__name__}: {exc}") from exc

    check_file = work / "check.json"
    proc = run_gate._shape(
        shape_cmd, ["check", str(profile), str(contract_file), "--json", str(check_file)], env
    )
    if proc.returncode not in (0, 1):
        raise GateError(f"shape check exited {proc.returncode}: {run_gate._tail(proc)}")
    violations = list(json.loads(check_file.read_text(encoding="utf-8")).get("violations", []))
    if proc.returncode == 1 and not violations:
        raise GateError("shape check exited 1 without violations")

    changes: list[dict[str, Any]] = []
    baseline = str(settings.get("baselineUrl") or "")
    if baseline:
        baseline_file, diff_file = work / "baseline.shape", work / "diff.json"
        run_gate._download(baseline, baseline_file, client_id)
        proc = run_gate._shape(
            shape_cmd, ["diff", str(baseline_file), str(profile), "--json", str(diff_file)], env
        )
        if proc.returncode != 0:
            raise GateError(f"shape diff exited {proc.returncode}: {run_gate._tail(proc)}")
        diff = json.loads(diff_file.read_text(encoding="utf-8"))
        gate["drifted"] = bool(diff.get("drifted"))
        changes = list(diff.get("changes", []))
        if gate["drifted"] and run_gate._as_bool(settings.get("failOnDrift", False)):
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
    gate["truncated"] = len(violations) > run_gate.MAX_LISTED or len(changes) > run_gate.MAX_LISTED
    gate["violations"] = violations[: run_gate.MAX_LISTED]
    gate["changes"] = changes[: run_gate.MAX_LISTED]
    return gate


def run(settings: dict[str, Any], shape_cmd: list[str], workdir: str | Path | None = None) -> int:
    """Generate and check, publish the files, return the exit code."""
    output = str(settings.get("outputUrl") or "")
    if not output:
        raise GateError("outputUrl is required")
    client_id = str(settings.get("managedIdentityClientId") or "")
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        work = Path(tmp)
        try:
            gate = evaluate(settings, work, shape_cmd)
            for parquet in sorted((work / DATA_DIR).glob("*.parquet")):
                run_gate._upload(parquet, run_gate.join_location(output, DATA_DIR), parquet.name, client_id)
            gate["contractUrl"] = run_gate._upload(
                work / CONTRACT_FILE, output, CONTRACT_FILE, client_id
            )
            gate["artifactUrl"] = run_gate._upload(
                work / run_gate.PROFILE_FILE, output, run_gate.PROFILE_FILE, client_id
            )
            run_gate._upload(work / run_gate.SUMMARY_FILE, output, run_gate.SUMMARY_FILE, client_id)
        except Exception as exc:  # noqa: BLE001 - exit 1 means a violation: a crash is exit 2
            message = str(exc) if isinstance(exc, GateError) else f"{type(exc).__name__}: {exc}"
            gate = run_gate._new_gate(message)
            gate["domain"] = str(settings.get("domain") or "")
            gate["tables"] = {}
        gate_file = work / run_gate.GATE_FILE
        gate_file.write_text(json.dumps(gate), encoding="utf-8")
        run_gate._upload(gate_file, output, run_gate.GATE_FILE, client_id)  # a failed gate says why
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
        settings = run_gate.load_settings(a.activity)
        return run(settings, a.shape.split())
    except GateError as exc:
        print(f"shape generate gate: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - never exit 1 (a violation) for an error
        print(f"shape generate gate: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
