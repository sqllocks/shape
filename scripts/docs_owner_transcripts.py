"""Capture owner examples verbatim; resolve a note only after successful real execution."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BLOCK = re.compile(
    r"<!-- owner-example: ([a-z0-9-]+) -->\n\n```bash {\.runnable-owner}\n(.*?)\n```"
    r'(?:(?:\n\n<!-- owner:[^\n]* -->)?\n\n\?\?\? info "Output \(exit '
    r'0\)"\n\n    ```text {\.expected}\n(.*?)    ```)?',
    re.S,
)


def machine() -> dict[str, Any]:
    """Record actual host and software details, never a guessed runner specification."""
    import pyarrow

    from shape.kernel import kernel_name

    cpu = platform.processor()
    if Path("/proc/cpuinfo").exists():
        cpu = next(
            (
                line.split(":", 1)[1].strip()
                for line in Path("/proc/cpuinfo").read_text().splitlines()
                if line.startswith("model name")
            ),
            cpu,
        )
    return {
        "cpu": cpu,
        "logical_cpus": os.cpu_count(),
        "ram_bytes": os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"),
        "os": platform.platform(),
        "python": platform.python_version(),
        "pyarrow": pyarrow.__version__,
        "kernel": kernel_name(),
        "runner_image": os.environ.get("ImageVersion", "not supplied"),
        "os_release": platform.freedesktop_os_release(),
    }


def capture(
    root: Path, manifest: list[dict[str, Any]], *, run_id: str, commit: str, runner: str
) -> int:
    """Write raw receipts for every attempt; retain notes and expected output on failure."""
    from docs_transcripts import comparable

    destination = root / "docs/plans/evidence/owner-notes"
    destination.mkdir(parents=True, exist_ok=True)
    failed = False
    host = machine()
    for item in manifest:
        page = root / item["page"]
        text = page.read_text()
        match = next((b for b in BLOCK.finditer(text) if b[1] == item["id"]), None)
        if match is None:
            raise ValueError(f"Missing runnable owner example {item['id']} in {item['page']}")
        work = root / ".owner-transcript-work" / item["id"]
        work.mkdir(parents=True, exist_ok=True)
        env = {
            **os.environ,
            "SHAPE_DOCS_REPO": str(root),
            "SHAPE_HOME": str(work / ".shape"),
            "SHAPE_JOBS_DIR": str(work / "jobs"),
            "NO_COLOR": "1",
            "COLUMNS": "100",
        }
        started = time.perf_counter()
        try:
            run = subprocess.run(
                ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", match[2]],
                cwd=work,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=item.get("timeout", 1200),
            )
            output, code = run.stdout, run.returncode
        except subprocess.TimeoutExpired as exc:
            output = (
                (exc.stdout or b"").decode()
                if isinstance(exc.stdout, bytes)
                else (exc.stdout or "")
            )
            output += "\nCommand exceeded its time limit; no successful transcript.\n"
            code = 124
        status = "passed" if code == 0 else "failed"
        # Never save credentials even if the product or a dependency prints one.
        for key, value in env.items():
            if (
                any(word in key.upper() for word in ("PASSWORD", "SECRET", "TOKEN"))
                and len(value) >= 4
                and value in output
            ):
                output = output.replace(value, "<redacted>")
                status = "credential-leak"
        if status == "passed" and match[3] is not None:
            expected = "\n".join(line.removeprefix("    ") for line in match[3].splitlines())
            if expected == "(no output)":
                expected = ""
            if comparable(output.rstrip("\n"), work, root) != comparable(
                expected, work, root, recorded=True
            ):
                status = "output-mismatch"
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
                    "page": item["page"],
                    "example": item["id"],
                    "command": match[2],
                    "exit_code": code,
                    "status": status,
                    "elapsed_seconds": time.perf_counter() - started,
                    "stdout": output,
                },
                indent=2,
            )
            + "\n"
        )
        print(f"{item['id']}: {status}, exit {code}; {receipt.relative_to(root)}", flush=True)
        if status != "passed":
            failed = True
            continue
        new_block = match[0]
        if match[3] is None and not item.get("raw_measurement"):
            shown = output.rstrip("\n") or "(no output)"
            new_block += (
                '\n\n??? info "Output (exit 0)"\n\n    ```text {.expected}\n'
                + "\n".join("    " + line for line in shown.splitlines())
                + "\n    ```"
            )
        text = text[: match.start()] + new_block + text[match.end() :]
        # Public pages may not link into docs/plans/, so the same receipt is published beside
        # the other public evidence and linked relatively.
        public = root / "docs/evidence/transcripts" / receipt.name
        public.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(receipt, public)
        target = Path(os.path.relpath(public, page.parent)).as_posix()
        link = f"Evidence: [run {run_id}, {runner}, `{commit[:12]}`]({target})."
        note = f"<!-- owner: {item['note']} -->"
        if note in text:
            text = text.replace(note, link, 1)
        else:
            # Re-run receipts refresh their link; existing expected output stays exact.
            text = re.sub(
                r"Evidence: \[run [^\n]+/docs-transcripts-[^\n]+-"
                + re.escape(item["id"])
                + r"\.json\)\.",
                link,
                text,
            )
        page.write_text(text)
    return int(failed)


def main() -> int:
    """Capture only the checked-in inventory on the explicitly assigned branch."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "tests/docs/owner_examples.json")
    args = parser.parse_args()
    return capture(
        ROOT,
        json.loads(args.manifest.read_text()),
        run_id=os.environ["GITHUB_RUN_ID"],
        commit=os.environ["GITHUB_SHA"],
        runner=os.environ["SHAPE_DOCS_RUNNER"],
    )


if __name__ == "__main__":
    raise SystemExit(main())
