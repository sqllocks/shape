"""The intentional differences between ``shape demo`` and the baseline's ``demo`` (P6-12).

Every entry has a name, the reason, and the probe in ``verify.py`` that shows it: an observation of
the baseline, so an entry that stops being true fails the run. Nothing outside this list may
differ: a difference that is not here is a failure. The list is the "named allow-list entry and its
reason" of the owner's standing decision (2026-10-01) about baseline defects that harm trust.

``BRAND`` is not a defect: Shape names itself where the baseline names itself (D-13), so the
baseline's text is mapped to Shape's before it is compared.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Difference:
    name: str
    command: str
    reason: str
    probe: str  # the function of verify.py that shows it


# (pattern, replacement) applied to the baseline's text before comparing it with Shape's.
BRAND: tuple[tuple[str, str], ...] = (
    (r"Spindle", "Shape"),
    (r"spindle", "shape"),
)


def brand(text: str) -> str:
    """The baseline's ``text`` with its own name mapped to Shape's."""
    for pattern, replacement in BRAND:
        text = re.sub(pattern, replacement, text)
    return text


ALLOWED: tuple[Difference, ...] = (
    Difference(
        "scenario-domain-ignored",
        "run",
        "the baseline generates retail whatever the scenario (it reads --domain, never the "
        "scenario's own domains, and --domains not at all), so `run healthcare` seeds retail "
        "tables and `run enterprise` one domain of three; Shape runs the domains the scenario "
        "names (or --domain, --domains) and fails when one is not installed",
        "probe_scenario_domain",
    ),
    Difference(
        "per-table-row-counts",
        "run (seeding)",
        "every artifact of a seeding run carries the run's total as its row count in the "
        "baseline (one number for all tables); Shape records the rows each table has",
        "probe_row_counts",
    ),
    Difference(
        "dry-run-in-every-mode",
        "run --dry-run, --estimate",
        "the baseline plans only a seeding run; an inference or streaming run with --dry-run "
        "runs for real and records artifacts; Shape plans every mode and generates nothing",
        "probe_dry_run",
    ),
    Difference(
        "unremoved-reported-removed",
        "cleanup",
        "the baseline lists a lakehouse or eventhouse artifact as removed after only logging that "
        "removal is not implemented, and an artifact it records under the label of its sink class "
        "(`sqldatabase`) is skipped by its own cleanup; Shape removes what it can reach, lists "
        "what it could not remove as failed (exit 1) and what it left alone as skipped",
        "probe_cleanup_honesty",
    ),
    Difference(
        "file-removal-by-name",
        "cleanup",
        "the baseline removes any folder whose path has a part named `spindle`; Shape removes only "
        "folders inside the session folder the run made (marked with the session id) and files "
        "inside the output folder the record names",
        "probe_file_removal",
    ),
    Difference(
        "spark-artifacts-recorded",
        "run --scale-mode spark",
        "the baseline records no artifact for a Spark run, so its cleanup can remove nothing; "
        "Shape records the Delta tables the notebook will write, and cleanup removes them",
        "probe_spark_artifacts (reads the baseline's source)",
    ),
    Difference(
        "failed-run-rolls-back",
        "run (seeding)",
        "the baseline rolls back only on an exception, and its modes catch every exception, so a "
        "seeding run that fails leaves what it wrote; Shape rolls back a failed run and says how "
        "many artifacts it removed",
        "probe_rollback (reads the baseline's source)",
    ),
    Difference(
        "no-silent-target-skip",
        "run (seeding)",
        "a Warehouse with no staging path or an Eventhouse with no database is skipped with a log "
        "line in the baseline while the run reports success; Shape fails and names the missing "
        "setting",
        "probe_silent_skip",
    ),
    Difference(
        "secrets-not-stored",
        "init",
        "the baseline stores a connection string that holds a password, in plain text in "
        "connections.json; Shape refuses it (give a credential reference or sign in with an auth "
        "method) and accepts a client secret only as a reference",
        "probe_secret_at_rest",
    ),
    Difference(
        "preflight-checks-what-it-reports",
        "preflight",
        "the baseline prints [OK] for an Eventhouse, a SQL database and a Lakehouse after checking "
        "only that a setting is not empty, and exits 0 when a check fails; Shape connects to "
        "each target, reports what it found, and exits 1 on a failure",
        "probe_preflight",
    ),
    Difference(
        "unknown-output-format-refused",
        "run --output",
        "an output format the baseline does not know is ignored (a typo shows nothing and exits "
        "0); Shape refuses it",
        "probe_output_format",
    ),
    Difference(
        "rows-zero",
        "run --rows",
        "`--rows 0` means the scenario's default in the baseline (`rows or default_rows`); Shape "
        "refuses it",
        "probe_rows_zero",
    ),
    Difference(
        "report-escaping",
        "report",
        "the HTML report prints the scenario, mode, error and names unescaped (markup in a name "
        "runs in the viewer), and a `|` or a line break in the Markdown report ends a table "
        "cell; Shape escapes both. A benign record gives the same report in both",
        "probe_report_escaping",
    ),
    Difference(
        "session-id-is-a-name",
        "status, report, cleanup",
        "the baseline puts any text into the record's file name (a `/` reads a file from a "
        "subfolder); Shape accepts only plain names and answers `no such session` otherwise",
        "probe_session_id",
    ),
    Difference(
        "exit-codes",
        "run, status, report, cleanup",
        "bad input is exit 2 with one line in Shape (an unknown scenario, session, profile or "
        "mode); the baseline raises (exit 1 with a traceback) or exits 1",
        "probe_exit_codes",
    ),
    Difference(
        "streaming-is-bounded-events",
        "run --mode streaming",
        "the baseline streams the whole first table ten times (about six seconds) with its own "
        "field names; Shape streams the first --max-events events (default 100) of one table in "
        "event-time order with `_shape_*` fields (D-12)",
        "probe_streaming",
    ),
    Difference(
        "charts-are-a-page",
        "run --output charts, semantic_model",
        "the baseline needs plotly and shows charts with fig.show(), and `semantic_model` writes "
        "nothing; Shape writes one self-contained HTML page and a Power BI model into "
        "--output-dir and records both",
        "probe_outputs",
    ),
    Difference(
        "notebook-is-a-python-notebook",
        "notebook",
        "the notebook has a Python 3 kernel (the other Shape notebooks' and what a Fabric Python "
        "notebook runs), installs the Shape packages, calls demo_run, and has a deterministic id "
        "per cell; the baseline declares Synapse PySpark metadata and random ids",
        "check_notebook",
    ),
    Difference(
        "html-declares-its-charset",
        "report --format html",
        "the report page declares UTF-8 (its title has an em dash); the baseline's page does not",
        "check_report",
    ),
    Difference(
        "added-settings",
        "init, run",
        "Shape adds --local-path, --warehouse-staging-path and --eventhouse-database (a folder "
        "target and the two settings a Warehouse and an Eventhouse need), --output-dir, "
        "--max-events and --json; the baseline's options are all kept",
        "check_options",
    ),
    Difference(
        "learn-rules",
        "run (inference)",
        "the learned schema follows the rules `shape.generation.learn.DIFFERENCES` names (P4-08), "
        "each with its reason; the one met on the same input is `truncated_enum`: a numeric column "
        "with more than 500 distinct values is generated from its distribution, where the baseline "
        "draws from the 500 most frequent values. Every other column has the same strategy",
        "check_inference_file",
    ),
    Difference(
        "own-domain-sample",
        "run (inference)",
        "with no --input-file a run profiles a sample of its own domain's output; the two tools' "
        "domains are statistically equivalent (T-21) but not equal, so a column near a profiler "
        "threshold can learn another strategy. The same file gives the same strategies",
        "check_inference",
    ),
    Difference(
        "artifact-order",
        "run (inference)",
        "artifacts follow the order Shape generates the tables in (dependency order); the same "
        "tables with the same rows",
        "check_inference",
    ),
)
