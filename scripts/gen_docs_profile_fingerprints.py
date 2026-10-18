"""Generate validated profile fingerprints from the existing, unchanged transcripts.

Run ``python scripts/gen_docs_profile_fingerprints.py`` in the pinned docs
environment, with its native NumPy and BLAS CPU dispatch. Every command must
match the already recorded transcript before any fingerprint is written. This
does not regenerate or rewrite documentation or expected output.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from docs_profile_fingerprints import BASELINE, identities
from docs_transcripts import comparable

ROOT = Path(__file__).resolve().parents[1]
PAGES = (
    "docs/tutorials/01-first-profile.md",
    "docs/tutorials/02-read-report.md",
    "docs/tutorials/03-drift.md",
    "docs/tutorials/04-contract.md",
    "docs/tutorials/05-generate-profile.md",
    "docs/HISTORY.md",
    "docs/DETECTIVE.md",
    "docs/PROFILE_MERGE.md",
    "docs/PROPOSALS.md",
)
BLOCK = re.compile(
    r"```bash {\.runnable(?:-reference)?}\n(.*?)\n```\n\n"
    r'\?\?\? info "Output \(exit (-?\d+)\)"\n\n'
    r"    ```text {\.expected}\n(.*?)    ```",
    re.S,
)


def main() -> None:
    result: dict[str, str] = {}
    sources: dict[str, list[str]] = {}
    for page in PAGES:
        with tempfile.TemporaryDirectory(prefix="docs-fingerprint-") as directory:
            work = Path(directory)
            env = {
                **os.environ,
                "SHAPE_KERNEL": "python",
                "SHAPE_DOCS_REPO": str(ROOT),
                "SHAPE_HOME": str(work / ".shape-state"),
                "SHAPE_JOBS_DIR": str(work / "jobs"),
                "SHAPE_PROFILE_REGISTRY": str(work / "profile-registry"),
                "SHAPE_HEALTHCARE_CODES_DIR": str(work / "healthcare-assets"),
                "PYTHONPATH": str(ROOT / "src"),
                "NO_COLOR": "1",
                "COLUMNS": "100",
            }
            recorded_ids: set[str] = set()
            for command, code, block in BLOCK.findall((ROOT / page).read_text(encoding="utf-8")):
                expected = "\n".join(line.removeprefix("    ") for line in block.splitlines())
                if expected == "(no output)":
                    expected = ""
                run = subprocess.run(
                    ["bash", "--noprofile", "--norc"]
                    + (["-e", "-o", "pipefail"] if "/tutorials/" in page else [])
                    + ["-c", command],
                    cwd=work,
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=1200,
                )
                actual = run.stdout
                if re.findall(r"\b[0-9a-f]{64}\b", actual) != re.findall(
                    r"\b[0-9a-f]{64}\b", expected
                ):
                    raise ValueError(f"recorded identities did not match: {page}\n{actual}")
                # Identities were compared exactly above; keep regeneration
                # independent of an existing fingerprint-baseline version.
                actual_without_ids = re.sub(r"\b[0-9a-f]{64}\b", "<checked-id>", actual)
                expected_without_ids = re.sub(r"\b[0-9a-f]{64}\b", "<checked-id>", expected)
                if run.returncode != int(code) or comparable(
                    actual_without_ids.rstrip("\n"), work, ROOT
                ) != comparable(expected_without_ids, work, ROOT, recorded=True):
                    raise ValueError(f"existing transcript did not match: {page}\n{actual}")
                recorded_ids.update(re.findall(r"\b[0-9a-f]{64}\b", expected))
                for cid, fingerprint in identities(work).items():
                    if cid in recorded_ids:
                        result[cid] = fingerprint
                        sources.setdefault(cid, [])
                        if page not in sources[cid]:
                            sources[cid].append(page)
            print(f"Validated unchanged transcript: {page}")
    (ROOT / BASELINE).write_text(
        json.dumps({"format": 1, "identities": result, "sources": sources}, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
