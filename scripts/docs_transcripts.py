"""Compare complete example transcripts, validating explicitly variable runtime fields."""

from __future__ import annotations

import re
from pathlib import Path

# Each expression matches a runtime field's actual type. Dataset values, findings,
# row counts, scores, exit codes and errors remain part of the exact comparison.
RUNTIME_FIELDS = (
    (r"(?m)^(  python    )\d+\.\d+\.\d+  \([^\n]+\)$", r"\1<python-version>  (<platform>)"),
    (r"(?m)^(    python    )\d+\.\d+\.\d+  \([^\n]+\)$", r"\1<python-version>  (<platform>)"),
    (r"\(\d+:\d{2}:\d{2}\)", "(<elapsed-clock>)"),
    (r"\[(?:\d+(?:\.\d+)?s|…)\]", "[<seconds>s]"),
    (r"\bSession: (?:[0-9a-f]{8}\b|…)", "Session: <session-id>"),
    (r"\bStarted: (?:\d{4}-\d{2}-\d{2}T[0-9:.+Z-]+|…)", "Started: <clock>"),
    (r"(?m)^(\S+\.whl  )(?:[\d,]+ bytes|…)$", r"\1…"),
    (r"\b\d{8}T\d{6}(?=_generate)", "<run-clock>"),
    (r'("(?:elapsed_seconds|total_elapsed_seconds)":\s*)\d+(?:\.\d+)?', r"\1<seconds>"),
    (r"\b[\d,]+ rows/s\b", "<rate> rows/s"),
    (r"\b\d+ thread\(s\)", "<worker-count> thread(s)"),
    (r"\b\d+ files already formatted\b", "<file-count> files already formatted"),
    (r"(\*\*Generated:\*\* )\d{4}-\d{2}-\d{2}T[0-9:.+Z-]+", r"\1<clock>"),
    (r"\bTime:\s+\d+(?:\.\d+)?s", "Time: <seconds>s"),
    (r"\(\d+(?:\.\d+)?s,", "(<seconds>s,"),
    (r"(?m)^sha256:[0-9a-f]{64}$", "sha256:<image-digest>"),
    (r"\b(?:local-[0-9a-f]{8}|job-[0-9a-f]{12})\b", "<job-id>"),
    (r"\b\d{8}_\d{6}(?=_retail_)", "<run-clock>"),
    (
        r"(\"(?:created_at|updated_at|started|finished|generated_at|built_at|at|timestamp)\":\s*\")"
        r"\d{4}-\d{2}-\d{2}T[0-9:.+Z-]+",
        r"\1<clock>",
    ),
    (r"\(\d+(?:\.\d+)?s\)", "(<seconds>s)"),
    (r"\b[\d,]+(?:\.\d+)? events/s\b", "<rate> events/s"),
    (r"\b(?:Elapsed|elapsed|duration|Duration):\s*\d+(?:\.\d+)?s", "elapsed: <seconds>s"),
    (r"\b\d+(?:\.\d+)? seconds\b", "<seconds> seconds"),
    (r"\b(in|after) (?:\d+(?:\.\d+)?|…)s\b", r"\1 <seconds>s"),
    (r"/tmp/tmp[a-z0-9_]+/profile\b", "<temporary-profile>/profile"),
    (r"/tmp/pytest-[^/]+/pytest-\d+/[^/]+/", "<pytest-work>/"),
    (r'File "[^"\n]*/([^/"\n]+)", line \d+', r'File "<python>/\1", line <line>'),
    (r"\b\d{2}:\d{2}:\d{2}\b(?=  )", "<clock>"),
)
CRYPTO_FIELDS = (
    (r'("(?:key_id|signed_by)":\s*")[0-9a-f]{16}', r"\1<key-id>"),
    (r"\b[0-9a-f]{16}\b(?=\s*\(Ed25519\))", "<key-id>"),
    (r"\bsha256:[0-9a-f]{64}\b", "sha256:<digest>"),
)


def comparable(text: str, work: Path, root: Path, *, crypto: bool = False) -> str:
    """Validate field shapes and canonicalise only declared runtime values."""
    text = text.replace(str(work), "<work>").replace(str(root), "<checkout>")
    text = re.sub(r"/tmp/docs-reference-runs/[^/\s\"']+", "<work>", text)
    for pattern, replacement in RUNTIME_FIELDS + (CRYPTO_FIELDS if crypto else ()):
        text = re.sub(pattern, replacement, text)
    return text
