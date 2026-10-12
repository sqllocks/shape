"""Lead-only release recorder. Builders test this program with isolated stub workers."""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import machine_meta  # noqa: E402 (harness path bootstrap)
from paths import (  # noqa: E402 (harness path bootstrap)
    BASELINE_PY,
    BASELINE_VERSION,
    BENCH_OUT_DIR,
    RECORDED,
    SHAPE_ROOT,
)
from recorded_store import (  # noqa: E402 (harness path bootstrap)
    RecordingError,
    check_all,
    filename,
    manifest,
    sha256,
)

FREEZE = BENCH_OUT_DIR / "baseline_freeze.txt"
SEEDS = (42, 43, 44, 45, 46)


def harness(name: str, relative: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, HERE / relative)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def profile_ids() -> list[str]:
    return list(harness("baseline_profile_plan", "profile_1to1/verify.py").ALL)


def domains() -> list[str]:
    return sorted(
        p.stem.removesuffix("_3nf") for p in (HERE / "fixtures" / "schemas").glob("*_3nf.json")
    )


def generation_ids() -> list[str]:
    return ["retail:small", *[d + ":medium" for d in domains()], "retail:large"]


def db_cases() -> dict[str, Any]:
    return dict(harness("baseline_db_plan", "db_1to1/verify.py").CASES)


def cli_cases() -> list[str]:
    return [r[0] for r in harness("baseline_verify_plan", "verify_1to1/verify.py").SCENARIOS]


def workloads() -> list[str]:
    return [
        *["profile:" + x for x in profile_ids()],
        *["generate:" + x for x in generation_ids()],
        *["stream:retail:order:" + x for x in ("small", "medium")],
        *["db:" + x + suffix for x in db_cases() for suffix in ("", ":mssql")],
        *["verify_cli:" + x for x in cli_cases()],
        "plan:composites",
        "timings:" + BASELINE_VERSION,
    ]


def worker_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    return env


def check_release() -> tuple[str, str]:
    r = subprocess.run(
        [str(BASELINE_PY), "-c", "import shape; print(shape.__version__)"],
        env=worker_env(),
        capture_output=True,
        text=True,
    )
    if r.returncode:
        raise RecordingError("baseline interpreter cannot import Shape; run setup_baseline.sh")
    version = r.stdout.strip()
    tag = subprocess.run(
        ["git", "rev-parse", "v" + BASELINE_VERSION + "^{commit}"],
        cwd=SHAPE_ROOT,
        capture_output=True,
        text=True,
    )
    return version, tag.stdout.strip() if tag.returncode == 0 else ""


def redact(text: str, connection: str | None = None) -> str:
    """Remove credentials from worker commands, exceptions and captured output."""
    for value in (connection, os.environ.get("BASELINE_MSSQL"), os.environ.get("SHAPE_TEST_MSSQL")):
        if not value:
            continue
        passwords = re.findall(r"(?i)(?:PWD|PASSWORD)\s*=\s*(\{(?:[^}]|}})*\}|[^;]*)", value)
        text = text.replace(value, "<redacted connection>")
        for password in passwords:
            if password:
                text = text.replace(password, "<redacted>")
                if password.startswith("{") and password.endswith("}"):
                    text = text.replace(password[1:-1].replace("}}", "}"), "<redacted>")
    return re.sub(r"(?i)\b(PWD|PASSWORD)\s*=\s*(\{(?:[^}]|}})*\}|[^;\s]*)", r"\1=<redacted>", text)


def redacted_command(command: list[str]) -> str:
    args = list(command)
    for i, arg in enumerate(args[:-1]):
        if arg == "--mssql":
            args[i + 1] = "<redacted connection>"
    return redact(shlex.join(args))


def worker_failure(
    kind: str, identifier: str, command: list[str], result: Any, connection: str | None = None
) -> None:
    output = (result.stdout or "") + "\n" + (result.stderr or "")
    tail = "\n".join(output.splitlines()[-200:])
    print(
        redact(
            f"{kind}:{identifier}: command {redacted_command(command)}; "
            f"exit {result.returncode}\n{tail}",
            connection,
        ),
        file=sys.stderr,
    )


def collect(
    kind: str,
    identifier: str,
    *,
    mssql: str | None = None,
    store: Path | None = None,
    smoke: bool = False,
) -> dict[str, Any]:
    env = worker_env()
    if mssql:
        env["BASELINE_MSSQL"] = mssql
    if store:
        env["BASELINE_RECORD_DIR"] = str(store)
    command = [
        str(BASELINE_PY),
        str(Path(__file__).resolve()),
        "--worker",
        kind,
        "--identifier",
        identifier,
    ]
    if smoke:
        command.append("--smoke")
    try:
        r = subprocess.run(command, env=env, capture_output=True, text=True)
    except OSError as exc:
        worker_failure(
            kind, identifier, command, subprocess.CompletedProcess(command, 2, "", str(exc)), mssql
        )
        raise RecordingError(
            kind + ":" + identifier + ": baseline worker could not start"
        ) from None
    if r.returncode:
        worker_failure(kind, identifier, command, r, mssql)
        raise RecordingError(
            kind + ":" + identifier + ": baseline worker failed (exit " + str(r.returncode) + ")"
        )
    try:
        payload = json.loads(r.stdout)
        if not isinstance(payload, dict):
            raise ValueError("worker must return an object")
        return payload
    except ValueError:
        worker_failure(kind, identifier, command, r, mssql)
        raise RecordingError(kind + ":" + identifier + ": malformed worker output") from None


def profile_worker(identifier: str) -> dict[str, Any]:
    v = harness("record_profile_worker", "profile_1to1/verify.py")
    try:
        profile = v.shape_profile(identifier)
    except Exception as exc:
        profile = {"__error__": {"category": v.sd.error_category(exc)}}
    return {
        "profile": profile,
        "inputs": v.input_digests(identifier),
        "input_digest": v._input_digest(identifier),
    }


def capture_identifier_form(gen: dict[str, Any], identifiers: str | None = None) -> dict[str, Any]:
    """Capture the release provider's exact template and complete component domains.

    Only the random selectors are replaced: every possible pool value and integer in the
    provider's declared half-open bounds reaches its original formatting/template code.
    This runs in the release recorder, never in the candidate verifier.
    """
    import numpy as np

    from shape.builtins.strategies import providers
    from shape.generation import kernel_ops
    from shape.generation.identifiers import DEFAULT_IDENTIFIERS

    def template(literals: Any, slots: Any, components: Any, rows: Any) -> dict[str, Any]:
        return {
            "generator": dict(gen),
            "identifiers": identifiers or DEFAULT_IDENTIFIERS,
            "literals": list(literals),
            "slots": [list(slot) for slot in slots],
            "components": [component.to_pylist() for component in components],
        }

    ctx = SimpleNamespace(
        columns={}, n_rows=1, row_start=0, identifiers=identifiers or DEFAULT_IDENTIFIERS
    )
    with (
        patch.object(providers, "_pick", lambda ctx, label, size: np.arange(size)),
        patch.object(providers, "_ints", lambda ctx, label, lo, hi: np.arange(lo, hi)),
        patch.object(kernel_ops, "pool_take", lambda values, indexes: values),
        patch.object(kernel_ops, "template_strings", template),
    ):
        fn = getattr(providers, "_" + gen["provider"])
        return cast(dict[str, Any], fn(ctx, gen))


def identifier_forms(raw: dict[str, Any], identifiers: str | None = None) -> list[dict[str, Any]]:
    forms = {}
    for table in raw["tables"].values():
        for column in table["columns"].values():
            gen = column["generator"]
            if gen.get("strategy") in ("faker", "native") and gen.get("provider") in (
                "email",
                "company_email",
                "phone_number",
                "uri",
            ):
                key = json.dumps(gen, sort_keys=True)
                forms[key] = capture_identifier_form(gen, identifiers)
    return [forms[key] for key in sorted(forms)]


def generation_worker(identifier: str) -> dict[str, Any]:
    from shape.generation.domains import load_domain
    from shape.generation.engine import Engine

    domain, scale = identifier.split(":")
    v = harness("record_generation_worker", "domain_1to1/verify.py")
    result = {}
    for seed in SEEDS:
        env = worker_env()
        command = [
            str(BASELINE_PY),
            str(HERE / "domain_1to1" / "generate.py"),
            "--impl",
            "shape",
            "--domain",
            domain,
            "--scale",
            scale,
            "--seed",
            str(seed),
        ]
        r = subprocess.run(command, env=env, capture_output=True, text=True)
        if r.returncode:
            worker_failure("generate", identifier + ":seed" + str(seed), command, r)
            raise RecordingError("generation worker failed")
        result[str(seed)] = v.run_digests(v.generate.out_dir("baseline", domain, scale, seed))
    return {
        "seeds": result,
        "identifier_forms": identifier_forms(
            v.load_schema_json(domain), Engine(load_domain(domain).schema).identifiers
        ),
        "inputs": {
            "fixtures/schemas/" + domain + "_3nf.json": sha256(
                HERE / "fixtures" / "schemas" / (domain + "_3nf.json")
            )
        },
    }


def stream_worker(identifier: str) -> dict[str, Any]:
    scale = identifier.rsplit(":", 1)[1]
    v = harness("record_stream_worker", "stream_1to1/verify.py")
    seeds: dict[str, Any] = {}
    for seed in SEEDS:
        seeds[str(seed)] = {}
        for variant, n in (("whole", None), ("prefix", v.sc.PREFIX_EVENTS[scale])):
            p = v.sc.out_file("baseline", scale, seed, n)
            v.run_command(v.command("baseline", scale, seed, p, n), p)
            seeds[str(seed)][variant] = v.event_digest(p)
    return {
        "seeds": seeds,
        "inputs": {
            "fixtures/schemas/retail_3nf.json": sha256(
                HERE / "fixtures" / "schemas" / "retail_3nf.json"
            )
        },
    }


def db_worker(identifier: str) -> dict[str, Any]:
    real = identifier.endswith(":mssql")
    case = identifier.removesuffix(":mssql")
    v = harness("record_db_worker", "db_1to1/verify.py")
    scenario, scale, kw, fail = v.CASES[case]
    connection = os.environ.get("BASELINE_MSSQL") if real else None
    if real and not connection:
        raise RecordingError("real SQL Server cases require --mssql")
    try:
        if real:
            v.load_real(connection, scenario, scale)
        profile = v.shape_side(scenario, scale, kw, fail, connection)
        inputs = v.scenario_inputs(scenario, scale)
        return {
            "profile": profile,
            "table_digests": v.scenario_table_digests(scenario, scale),
            "inputs": inputs,
        }
    finally:
        if real:
            v.cleanup_real(connection, scenario, scale)


def pools_worker(domain: str) -> dict[str, Any]:
    from shape.builtins.strategies.providers import pool
    from shape.generation.domains import load_domain
    from shape.generation.reference import load_dataset

    dom = load_domain(domain)
    names = {
        key: pool(name).to_pylist()
        for key, name in {
            "FIRST_NAMES": "first_names",
            "LAST_NAMES": "last_names",
            "COMPANY_NAMES": "company_names",
            "SENTENCES": "sentences",
            "STREET_NAMES": "street_names",
        }.items()
    }
    datasets = {}
    for table in dom.schema.tables.values():
        for column in table.columns.values():
            name = column.generator.get("dataset")
            if name and name not in datasets:
                ds = load_dataset(name)
                import pyarrow as pa  # type: ignore[import-untyped]

                datasets[name] = (
                    pa.table(ds.columns).to_pylist()
                    if ds.records
                    else ds.column(ds.VALUE).to_pylist()
                )
    return {
        "names": names,
        "native": {
            key: pool(name).to_pylist()
            for key, name in {
                "_US_CITIES": "us_cities",
                "_US_STATES": "us_states",
                "_STREET_SUFFIXES": "street_suffixes",
            }.items()
        },
        "datasets": datasets,
    }


def plan_worker() -> dict[str, Any]:
    from shape.generation.engine import Engine

    importer = harness("record_composite_importer", "schema_import.py")
    plans = {}
    inputs = {}
    for path in sorted((HERE / "fixtures/composites").glob("*.json")):
        schema = importer.import_dump(json.loads(path.read_text())["schema"])
        counts = {
            scale: {table: int(engine.row_counts[table]) for table in schema.tables}
            for scale in schema.generation.scales
            for engine in [Engine(schema, scale=scale, seed=42)]
        }
        digest = sha256(path)
        plans[path.stem] = {"schema_sha256": digest, "planned": counts}
        inputs[path.relative_to(HERE).as_posix()] = digest
    return {"composites": plans, "inputs": inputs}


def cli_worker(identifier: str) -> dict[str, Any]:
    v = harness("record_cli_worker", "verify_1to1/verify.py")
    scenario = next(row for row in v.SCENARIOS if row[0] == identifier)
    return cast(dict[str, Any], v.capture_baseline_scenario(*scenario))


def timings_worker(smoke: bool = False) -> dict[str, Any]:
    # paths is already imported by this worker: changing only os.environ is insufficient.
    import paths

    saved_paths = (paths.SHAPE_VENV, paths.SHAPE_PY)
    saved_env = {key: os.environ.get(key) for key in ("SHAPE_VENV", "SHAPE_PY")}
    try:
        paths.SHAPE_VENV = BASELINE_PY.parent.parent
        paths.SHAPE_PY = BASELINE_PY
        os.environ["SHAPE_VENV"] = str(paths.SHAPE_VENV)
        os.environ["SHAPE_PY"] = str(BASELINE_PY)
        import run

        dest = BENCH_OUT_DIR / "baseline_timings.json"
        import contextlib

        with contextlib.ExitStack() as stack:
            if smoke:
                for name, value in (
                    (
                        "QUICK_PROFILE_DATASETS",
                        ["edge/e3.csv", "edge/x_pq_list.parquet", "edge/x_pq_nested.parquet"],
                    ),
                    ("QUICK_DOMAINS", [("retail", ["small"])]),
                    ("QUICK_STREAM_SCALES", ["small"]),
                ):
                    stack.enter_context(patch.object(run, name, value))
            rc = run.main(["--quick" if smoke else "--full", "--baseline-only", "--out", str(dest)])
        if rc:
            raise RecordingError("release timings failed verification; no timing snapshot")
        return cast(dict[str, Any], json.loads(dest.read_text()))
    finally:
        paths.SHAPE_VENV, paths.SHAPE_PY = saved_paths
        for key, previous in saved_env.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous


def worker(kind: str, identifier: str, domain: str | None, smoke: bool = False) -> int:
    import io

    captured = io.StringIO()
    try:
        import shape

        if shape.__version__ != BASELINE_VERSION:
            raise RecordingError("wrong release version")
        import contextlib

        with contextlib.redirect_stdout(captured):
            if kind == "pools":
                result = pools_worker(domain or identifier)
            elif kind == "profile":
                result = profile_worker(identifier)
            elif kind == "generate":
                result = generation_worker(identifier)
            elif kind == "stream":
                result = stream_worker(identifier)
            elif kind == "db":
                result = db_worker(identifier)
            elif kind == "verify_cli":
                result = cli_worker(identifier)
            elif kind == "plan":
                result = plan_worker()
            else:
                result = timings_worker(smoke) if smoke else timings_worker()
        print(json.dumps(result, sort_keys=True, default=str))
        return 0
    except Exception:
        print(
            redact(
                kind
                + ":"
                + identifier
                + ": baseline worker unavailable\n"
                + "\n".join(
                    (captured.getvalue() + "\n" + traceback.format_exc()).splitlines()[-200:]
                )
            ),
            file=sys.stderr,
        )
        return 2


def prepare_smoke_profile() -> None:
    command = [str(BASELINE_PY), str(HERE / "profile_1to1" / "datasets.py"), "EDGE", "EDGE2"]
    result = subprocess.run(command, env=worker_env(), capture_output=True, text=True)
    if result.returncode:
        worker_failure("profile", "edge/e3.csv inputs", command, result)
        raise RecordingError("profile:edge/e3.csv: input preparation failed")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--all", action="store_true")
    for kind in ("profile", "generate", "stream", "db", "verify-cli", "plan", "timings"):
        ap.add_argument("--" + kind, action="store_true")
    ap.add_argument("--mssql")
    ap.add_argument("--out", type=Path, default=RECORDED)
    ap.add_argument("--replace", action="store_true")
    ap.add_argument(
        "--smoke",
        action="store_true",
        help="pre-check selected workers on small inputs without publishing",
    )
    ap.add_argument(
        "--worker",
        choices=("profile", "generate", "stream", "db", "verify_cli", "plan", "timings", "pools"),
    )
    ap.add_argument("--identifier", default="")
    ap.add_argument("--domain")
    a = ap.parse_args(argv)
    if a.worker:
        return worker(a.worker, a.identifier, a.domain, a.smoke)
    stage = None
    try:
        version, commit = check_release()
        if version != BASELINE_VERSION:
            raise RecordingError("baseline version " + version + "; required " + BASELINE_VERSION)
        if not commit:
            raise RecordingError("missing tag v" + BASELINE_VERSION)
        if a.all and not a.mssql:
            raise RecordingError("--all requires --mssql for real SQL Server 2022 cases")
        existing = a.out.exists() and any(
            p.name not in ("check.py", "__pycache__") for p in a.out.iterdir()
        )
        if existing and not a.replace and not a.smoke:
            raise RecordingError("existing recording store; use --replace")
        selected = []
        if a.all or a.profile:
            selected += [("profile", x) for x in profile_ids()]
        if a.all or a.generate:
            selected += [("generate", x) for x in generation_ids()]
        if a.all or a.stream:
            selected += [("stream", "retail:order:" + x) for x in ("small", "medium")]
        if a.all or a.db:
            selected += [
                ("db", x + suffix)
                for x in db_cases()
                for suffix in (("", ":mssql") if a.mssql else ("",))
            ]
        if a.all or a.verify_cli:
            selected += [("verify_cli", x) for x in cli_cases()]
        if a.all or a.plan:
            selected += [("plan", "composites")]
        if not selected and not a.timings:
            raise RecordingError("select --all or a recording family")
        if a.smoke:
            selected_kinds = {kind for kind, _ in selected}
            selected = [
                (kind, identifier)
                for kind, identifier in (
                    ("profile", "edge/e3.csv"),
                    ("profile", "edge/x_pq_list.parquet"),
                    ("profile", "edge/x_pq_nested.parquet"),
                    ("generate", "retail:small"),
                    ("stream", "retail:order:small"),
                    ("db", "name_only"),
                    ("db", "name_only:mssql"),
                    ("verify_cli", cli_cases()[0]),
                    ("plan", "composites"),
                )
                if kind in selected_kinds and (not identifier.endswith(":mssql") or a.mssql)
            ]
            if any(kind == "profile" for kind, _ in selected):
                prepare_smoke_profile()
        now = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        a.out.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix="shape-baseline-record-", dir=a.out.parent))
        files: dict[str, Any] = {}
        ids: list[str] = []
        fixtures = {
            p.relative_to(HERE).as_posix(): {"sha256": sha256(p)}
            for p in (HERE / "fixtures").rglob("*")
            if p.is_file()
        }
        freeze_sha = sha256(FREEZE)
        meta = {
            "format": "shape-baseline-record",
            "version": 1,
            "shape_version": version,
            "shape_commit": commit,
            "source_release": {"version": version, "commit": commit},
            "recorded_utc": now,
            "machine": machine_meta(),
            "baseline_freeze_sha256": freeze_sha,
            "files": files,
            "fixtures": fixtures,
            "workloads": ids,
        }
        if a.timings and not selected:
            # Selectable timing capture reuses only an authenticated record of this release.
            previous = manifest(a.out)
            problems = check_all(a.out)
            if problems:
                raise RecordingError("; ".join(problems))
            for name, entry in previous["files"].items():
                if name.startswith("timings_"):
                    continue
                target = stage / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(a.out / name, target)
                files[name] = dict(entry)
            ids.extend(w for w in previous["workloads"] if not w.startswith("timings:"))

        def save(kind: str, identifier: str, payload: dict[str, Any]) -> None:
            wid = kind + ":" + identifier
            ids.append(wid)
            record = {
                "format": "shape-" + kind + "-record",
                "version": 1,
                "workload": wid,
                "shape_version": version,
                "shape_commit": commit,
                "recorded_utc": now,
                "inputs": {},
                **payload,
            }
            name = (
                filename(kind, identifier) if kind != "timings" else "timings_" + version + ".json"
            )
            assert stage is not None
            p = stage / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(record, sort_keys=True, indent=2, default=str) + "\n")
            files[name] = {"sha256": sha256(p), "bytes": p.stat().st_size}

        for kind, identifier in selected:
            save(kind, identifier, collect(kind, identifier, mssql=a.mssql))
        if a.smoke and (a.all or a.generate):
            collect("pools", "retail")
        if a.all or a.timings:
            # Authenticate staged inputs before timing; publish the final manifest last.
            (stage / "MANIFEST.json").write_text(json.dumps(meta, sort_keys=True, indent=2) + "\n")
            payload = (
                collect("timings", version, store=stage, smoke=True)
                if a.smoke
                else collect("timings", version, store=stage)
            )
            save("timings", version, {"results": payload})
        (stage / "MANIFEST.json").write_text(json.dumps(meta, sort_keys=True, indent=2) + "\n")
        problems = check_all(stage)
        if problems:
            raise RecordingError("; ".join(problems))
        if a.smoke:
            print("baseline smoke passed; no recording published")
            return 0
        # Rename a fully checked sibling store into place; preserve the checker code.
        if (a.out / "check.py").is_file():
            shutil.copy2(a.out / "check.py", stage / "check.py")
        backup = None
        if a.out.exists():
            backup = Path(tempfile.mkdtemp(prefix="shape-baseline-previous-", dir=a.out.parent))
            backup.rmdir()
            a.out.rename(backup)
        try:
            stage.rename(a.out)
            stage = None
        except OSError:
            if backup:
                backup.rename(a.out)
            raise
        if backup:
            shutil.rmtree(backup)
        print(
            f"recorded {len(files)} files / {sum(x['bytes'] for x in files.values())} bytes "
            f"from Shape {version} ({commit})"
        )
        return 0
    except (RecordingError, OSError, ValueError, KeyError) as exc:
        print(
            str(exc) if isinstance(exc, RecordingError) else "recording failed before publication",
            file=sys.stderr,
        )
        return 2
    finally:
        if stage:
            shutil.rmtree(stage, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
