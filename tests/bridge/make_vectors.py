"""Regenerate the published test vectors: ``python tests/bridge/make_vectors.py``.

It runs the requests below against a bridge and stores what comes back (volatile values as
``"<any>"``). Review the diff: a vector that changes is a change of the protocol."""

from __future__ import annotations

import csv
import json
import random
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "scale"))

import vectors_1_1 as v11  # noqa: E402
import vectors_1_2 as v12  # noqa: E402
import vectors_lib as lib  # noqa: E402
from fakes import LH, WS, FakeFabric  # noqa: E402
from scale_schemas import plain_doc  # noqa: E402

from shape.bridge.protocol import API_VERSION  # noqa: E402
from shape.bridge.registry import COMMANDS  # noqa: E402

D = "${DIR}"
ROWS = {"customer": 40, "order": 1200, "order_line": 3100}


def job(job_id: str, command: str, status: str, **extra: Any) -> dict[str, Any]:
    return {
        "format": "shape-bridge-job",
        "version": 1,
        "job_id": job_id,
        "command": command,
        "status": status,
        "created_at": "2026-01-01T00:00:00.000+00:00",
        "updated_at": "2026-01-01T00:00:01.000+00:00",
        "request": {"args": {}, "options": {}},
        "progress": {},
        "result": None,
        "error": None,
        "worker": {"pid": 1},
        "external": None,
        "cancellable": False,
        **extra,
    }


JOBS = {
    "job-000000000001": job(
        "job-000000000001", "scale_generate", "succeeded", result={"rows_generated": 10}
    ),
    "job-000000000002": job(
        "job-000000000002",
        "stream",
        "succeeded",
        result={"chunks_written": 2, "rows_written": 100, "stopped": False},
    ),
    "job-000000000003": job(
        "job-000000000003",
        "verify",
        "failed",
        error={
            "code": "input.not_found",
            "group": "input",
            "message": "file not found: x.csv",
            "hint": None,
        },
    ),
}


def case(
    name: str,
    command: str,
    args: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    version: str | None = None,
    **flags: Any,
) -> dict[str, Any]:
    """A vector case. Its request declares the version of the command (a 1.1 command declares
    1.1), or ``version``: a 1.0 command's vectors keep declaring 1.0, which is the 1.0 promise."""
    request: dict[str, Any] = {
        "api_version": version or COMMANDS[command].since,
        "id": name,
        "command": command,
        "args": args or {},
    }
    if options:
        request["options"] = options
    return {"name": name, "request": request, **flags}


PROFILE_A = {
    "api_version": "1.0",
    "id": "setup",
    "command": "profile",
    "args": {"source": f"{D}/a.csv", "output": f"{D}/a.shape"},
}
PROFILE_B = {
    "api_version": "1.0",
    "id": "setup",
    "command": "profile",
    "args": {"source": f"{D}/b.csv", "output": f"{D}/b.shape"},
}
SPARK = {"workspace_id": WS, "lakehouse_id": LH, "token": "vector-token"}

FILES: dict[str, dict[str, Any]] = {
    "list": {
        "cases": [
            case("domains", "list"),
            case("takes-no-arguments", "list", {"x": 1}, valid_request=False),
        ]
    },
    "describe": {
        "cases": [
            case("retail-small", "describe", {"domain": "retail", "scale": "small"}),
            case("schema-file", "describe", {"domain": f"{D}/schema.json"}),
            case("unknown-domain", "describe", {"domain": "nope"}),
            case(
                "unknown-argument",
                "describe",
                {"domain": "retail", "colour": "red"},
                valid_request=False,
            ),
        ]
    },
    "dry_run": {
        "cases": [
            case("retail-small", "dry_run", {"domain": "retail", "scale": "small"}),
            case("unknown-scale", "dry_run", {"domain": "retail", "scale": "gigantic"}),
        ]
    },
    "validate": {
        "cases": [
            case("good-schema", "validate", {"schema_path": f"{D}/schema.json"}),
            case("missing-file", "validate", {"schema_path": f"{D}/missing.json"}),
        ]
    },
    "generate": {
        "cases": [
            case("summary", "generate", {"domain": "retail", "scale": "small", "seed": 1}),
            case(
                "csv-files",
                "generate",
                {
                    "domain": "retail",
                    "scale": "small",
                    "seed": 1,
                    "format": "csv",
                    "output_dir": f"{D}/out",
                },
            ),
            case(
                "format-needs-a-directory",
                "generate",
                {"domain": "retail", "scale": "small", "format": "csv"},
            ),
        ]
    },
    "preview": {
        "cases": [
            case(
                "two-stores",
                "preview",
                {"domain": "retail", "rows": 2, "tables": ["store"], "seed": 1},
            ),
            case("unknown-table", "preview", {"domain": "retail", "tables": ["ghost"]}),
        ]
    },
    "profile_info": {
        "cases": [
            case("retail", "profile_info", {"domain": "retail"}),
            case("unknown-profile", "profile_info", {"domain": "retail", "profile": "ghost"}),
        ]
    },
    "demo_list": {"cases": [case("pending", "demo_list")]},
    "demo_run": {
        "cases": [
            case("pending", "demo_run", {"scenario": "retail", "rows": 100, "dry_run": True}),
            case("bad-rows", "demo_run", {"rows": "100"}, valid_request=False),
        ]
    },
    "demo_status": {
        "cases": [
            case("pending", "demo_status", {"session_id": "s-1"}),
            case("needs-a-session", "demo_status", {}, valid_request=False),
        ]
    },
    "demo_cleanup": {
        "cases": [
            case("pending", "demo_cleanup", {"session_id": "s-1", "dry_run": True}),
            case("needs-a-session", "demo_cleanup", {}, valid_request=False),
        ]
    },
    "scale_generate": {
        "cases": [
            case(
                "local-single-memory",
                "scale_generate",
                {"domain": f"{D}/schema.json", "scale_mode": "local_single"},
            ),
            case(
                "local-mp-parquet",
                "scale_generate",
                {
                    "domain": f"{D}/schema.json",
                    "scale_mode": "local_mp",
                    "chunk_size": 1000,
                    "sinks": ["parquet"],
                    "sink_config": {"parquet": {"output_dir": f"{D}/scaled"}},
                },
            ),
            case(
                "fabric-spark",
                "scale_generate",
                {
                    "domain": f"{D}/schema.json",
                    "scale_mode": "fabric_spark",
                    "sinks": ["lakehouse"],
                    "sink_config": SPARK,
                },
                needs="fabric",
            ),
            case(
                "fabric-without-a-token",
                "scale_generate",
                {
                    "domain": f"{D}/schema.json",
                    "scale_mode": "fabric_spark",
                    "sink_config": {"workspace_id": WS, "lakehouse_id": LH},
                },
            ),
            case(
                "unknown-mode",
                "scale_generate",
                {"domain": "retail", "scale_mode": "teleport"},
                valid_request=False,
            ),
        ]
    },
    "stream": {
        "cases": [
            case(
                "start",
                "stream",
                {
                    "domain": "retail",
                    "scale": "small",
                    "interval_seconds": 0,
                    "chunk_size": 20,
                    "max_chunks": 1,
                },
            ),
            case("unknown-domain", "stream", {"domain": "nope"}),
        ]
    },
    "stream_status": {
        "jobs": JOBS,
        "cases": [
            case("finished-stream", "stream_status", {"stream_id": "job-000000000002"}),
            case("unknown-stream", "stream_status", {"stream_id": "job-ffffffffffff"}),
        ],
    },
    "stream_stop": {
        "jobs": JOBS,
        "cases": [
            case("finished-stream", "stream_stop", {"stream_id": "job-000000000002"}),
            case("not-a-stream", "stream_stop", {"stream_id": "job-000000000001"}),
        ],
    },
    "scale_status": {
        "jobs": JOBS,
        "cases": [
            case("finished-job", "scale_status", {"job_id": "job-000000000001"}),
            case("not-a-scale-job", "scale_status", {"job_id": "job-000000000003"}),
        ],
    },
    "scale_cancel": {
        "jobs": JOBS,
        "cases": [
            case("finished-job", "scale_cancel", {"job_id": "job-000000000001"}),
            case("not-a-scale-job", "scale_cancel", {"job_id": "job-000000000002"}),
        ],
    },
    "profile": {
        "cases": [
            case("a-csv", "profile", {"source": f"{D}/a.csv", "output": f"{D}/a.shape"}),
            case(
                "missing-source",
                "profile",
                {"source": f"{D}/missing.csv", "output": f"{D}/m.shape"},
            ),
        ]
    },
    "diff": {
        "setup": [PROFILE_A, PROFILE_B],
        "cases": [
            case("drift", "diff", {"before": f"{D}/a.shape", "after": f"{D}/b.shape"}),
            case("no-drift", "diff", {"before": f"{D}/a.shape", "after": f"{D}/a.shape"}),
            case("missing-profile", "diff", {"before": f"{D}/none.shape", "after": f"{D}/a.shape"}),
        ],
    },
    "check": {
        "setup": [PROFILE_A],
        "cases": [
            case("passes", "check", {"profile": f"{D}/a.shape", "contract": f"{D}/contract.json"}),
            case(
                "fails", "check", {"profile": f"{D}/a.shape", "contract": f"{D}/contract_bad.json"}
            ),
            case(
                "missing-contract",
                "check",
                {"profile": f"{D}/a.shape", "contract": f"{D}/none.json"},
            ),
        ],
    },
    "verify": {
        "cases": [
            case("csv", "verify", {"path": f"{D}/a.csv"}),
            case("missing-data", "verify", {"path": f"{D}/missing.csv"}),
        ]
    },
    "job_status": {
        "jobs": JOBS,
        "cases": [
            case("failed-job", "job_status", {"job_id": "job-000000000003"}),
            case("unknown-job", "job_status", {"job_id": "job-ffffffffffff"}),
        ],
    },
    "job_cancel": {
        "jobs": JOBS,
        "cases": [
            case("finished-job", "job_cancel", {"job_id": "job-000000000001"}),
            case("unknown-job", "job_cancel", {"job_id": "job-ffffffffffff"}),
        ],
    },
    "job_list": {
        "jobs": JOBS,
        "cases": [
            case("all", "job_list"),
            case("failed-only", "job_list", {"status": "failed"}),
            case("unknown-status", "job_list", {"status": "paused"}, valid_request=False),
        ],
    },
}


def merge_1_1() -> None:
    """Add the vectors of the 1.1 commands, and the 1.1 cases of the 1.0 commands."""
    for command, doc in v11.FILES.items():
        FILES[command] = doc
    for command, extra in v11.EXTENDS.items():
        FILES[command]["setup"] = [*FILES[command].get("setup", []), *extra.get("setup", [])]
        FILES[command]["cases"] = [*FILES[command]["cases"], *extra["cases"]]


def merge_1_2() -> None:
    """Add the vectors of the 1.2 commands, and the 1.2 cases of earlier commands."""
    for command, doc in v12.FILES.items():
        FILES[command] = doc
    for command, extra in v12.EXTENDS.items():
        FILES[command]["setup"] = [*FILES[command].get("setup", []), *extra.get("setup", [])]
        FILES[command]["cases"] = [*FILES[command]["cases"], *extra["cases"]]


def write_fixtures() -> None:
    folder = lib.VECTOR_DIR / "fixtures"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "schema.json").write_text(
        json.dumps(plain_doc(ROWS), indent=2, sort_keys=True) + "\n"
    )
    (folder / "contract.json").write_text(
        json.dumps(
            {
                "row_count": {"min": 10, "max": 100},
                "columns": {"id": {"dtype": "integer", "unique": True}},
            },
            indent=2,
        )
        + "\n"
    )
    (folder / "contract_bad.json").write_text(
        json.dumps({"row_count": {"min": 1000}}, indent=2) + "\n"
    )
    for name, shift in (("a.csv", 0), ("b.csv", 1)):
        rng = random.Random(1)
        with open(folder / name, "w", newline="") as handle:
            out = csv.writer(handle)
            out.writerow(["id", "email", "status", "amount"])
            for i in range(40):
                status = rng.choice(["new", "paid", "refund" if shift else "void"])
                out.writerow(
                    [i, f"user{i}@example.com", status, round(rng.gauss(100 + shift * 50, 20))]
                )
    v11.write_fixtures(folder)
    v12.write_fixtures(folder)


def run_case(
    bridge: Any, doc: dict[str, Any], one: dict[str, Any], directory: str
) -> dict[str, Any]:
    response = bridge.handle(lib.substitute(one["request"], directory))
    return lib.abstract(response, directory)


def main() -> None:
    import os
    from unittest import mock

    merge_1_1()
    merge_1_2()
    write_fixtures()
    for command, doc in FILES.items():
        with tempfile.TemporaryDirectory() as tmp:
            directory = str(Path(tmp) / "work")
            jobs = lib.prepare(Path(directory), doc)
            from shape.bridge.core import Bridge

            bridge = Bridge(jobs)
            for setup in doc.get("setup", []):
                assert bridge.handle(lib.substitute(setup, directory))["ok"]
            cases = []
            for one in doc["cases"]:
                with mock.patch.dict(
                    os.environ, {"SHAPE_FABRIC_STORAGE_TOKEN": "stor"}, clear=False
                ):
                    os.environ.pop("SHAPE_FABRIC_TOKEN", None)
                    if one.get("needs") == "fabric":
                        with mock.patch("shape.scale.http.urllib_transport", FakeFabric()):
                            response = run_case(bridge, doc, one, directory)
                    else:
                        response = run_case(bridge, doc, one, directory)
                cases.append({**one, "response": response})
            deadline = time.time() + 60
            while time.time() < deadline and any(
                j["status"] == "running" for j in bridge.jobs.list()
            ):
                time.sleep(0.05)  # a stream job writes into the scratch directory until it ends
            out = {
                "format": "shape-bridge-vectors",
                "version": 1,
                "api_version": API_VERSION,
                "command": command,
                **({"jobs": doc["jobs"]} if doc.get("jobs") else {}),
                **({"setup": doc["setup"]} if doc.get("setup") else {}),
                "cases": cases,
            }
            (lib.VECTOR_DIR / f"{command}.json").write_text(
                json.dumps(out, indent=2, sort_keys=True) + "\n"
            )
    print(f"wrote {len(FILES)} vector files to {lib.VECTOR_DIR}")


if __name__ == "__main__":
    main()
