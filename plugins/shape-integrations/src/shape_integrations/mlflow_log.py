"""``shape mlflow log MANIFEST.json [--profile P.shape] [--verify-report R.json] ...``.

Logs one finished run to MLflow, after the fact:

* params: the reproducibility tuple of the manifest (when it has one);
* metrics: from the verify report, ``passed`` and, per gate, ``gate.<name>.passed``,
  ``gate.<name>.errors``, ``gate.<name>.warnings`` and every numeric entry of the gate's details
  as ``gate.<name>.<key>``;
* artifacts: the manifest, the profile and the verify report, as given;
* tags: ``shape.run_id`` and, when the manifest has it, ``shape.dataset_id``.

A second log of the same run id to the same experiment is refused unless ``--allow-duplicate``.

Exit codes: 0 logged; 1 MLflow failed or the run is a duplicate; 2 the input is wrong or the
``mlflow`` extra is missing. Nothing is logged beyond the list above.
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path
from typing import Any

from .extras import MissingExtraError, require
from .manifest_view import ManifestError, ManifestView, load

SHAPE_API = "1.0"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_INPUT = 2

DEFAULT_EXPERIMENT = "shape"
RUN_ID_TAG = "shape.run_id"
DATASET_ID_TAG = "shape.dataset_id"
_BAD_NAME_CHARS = re.compile(r"[^A-Za-z0-9_\-. /]")


class ReportError(ValueError):
    """The verify report is not usable."""


class DuplicateRunError(Exception):
    """The run id is already logged to the experiment."""


def _name(text: str) -> str:
    return _BAD_NAME_CHARS.sub("_", text)


def _numbers(prefix: str, value: Any, out: dict[str, float]) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        if math.isfinite(value):
            out[prefix] = float(value)
    elif isinstance(value, dict):
        for key, inner in value.items():
            _numbers(f"{prefix}.{_name(str(key))}", inner, out)


def metrics_from_report(report: Any) -> dict[str, float]:
    """The metrics of a ``shape verify`` JSON report; raises :class:`ReportError`."""
    if not isinstance(report, dict) or not isinstance(report.get("gates"), list):
        raise ReportError("not a verify report: expected a JSON object with a 'gates' list")
    out: dict[str, float] = {}
    if isinstance(report.get("passed"), bool):
        out["passed"] = 1.0 if report["passed"] else 0.0
    for gate in report["gates"]:
        if not isinstance(gate, dict) or not isinstance(gate.get("gate"), str):
            raise ReportError("not a verify report: every gate needs a 'gate' name")
        base = f"gate.{_name(gate['gate'])}"
        if isinstance(gate.get("passed"), bool):
            out[f"{base}.passed"] = 1.0 if gate["passed"] else 0.0
        for key in ("errors", "warnings"):
            if isinstance(gate.get(key), list):
                out[f"{base}.{key}"] = float(len(gate[key]))
        _numbers(base, gate.get("details") or {}, out)
    return out


def params_of(view: ManifestView) -> dict[str, str]:
    return {k: str(v) for k, v in sorted(view.reproducibility.items())}


def _read_inputs(args: Any) -> tuple[ManifestView, Path | None, Path | None, dict[str, float]]:
    view = load(args.manifest)
    profile = Path(args.profile) if args.profile else None
    if profile is not None and not profile.is_file():
        raise ManifestError(f"profile not found: {profile}")
    report_path = Path(args.verify_report) if args.verify_report else None
    metrics: dict[str, float] = {}
    if report_path is not None:
        if not report_path.is_file():
            raise ManifestError(f"verify report not found: {report_path}")
        try:
            metrics = metrics_from_report(json.loads(report_path.read_text(encoding="utf-8")))
        except (ValueError, OSError) as exc:  # includes JSON errors and ReportError
            raise ReportError(f"{report_path}: {exc}") from None
    return view, profile, report_path, metrics


def _already_logged(client: Any, experiment_id: str, shape_run_id: str) -> bool:
    """Whether a run tagged with ``shape_run_id`` is in the experiment.

    The run id is compared here, never put into MLflow's filter string, whose quoting rules
    would let an unusual id match nothing (or something else).
    """
    token = None
    while True:
        page = client.search_runs(
            [experiment_id],
            filter_string=f"tags.`{RUN_ID_TAG}` != ''",
            max_results=1000,
            page_token=token,
        )
        if any(r.data.tags.get(RUN_ID_TAG) == shape_run_id for r in page):
            return True
        token = page.token
        if not token:
            return False


def log_run(
    view: ManifestView,
    *,
    profile: Path | None,
    report: Path | None,
    metrics: dict[str, float],
    experiment: str,
    tracking_uri: str | None,
    allow_duplicate: bool,
) -> str:
    """Log one run and return its MLflow run id."""
    require("mlflow", "mlflow", name="MLflow")
    from mlflow.tracking import MlflowClient

    client = MlflowClient(tracking_uri=tracking_uri)
    found = client.get_experiment_by_name(experiment)
    if found is None:
        experiment_id = client.create_experiment(experiment)
    else:
        experiment_id = found.experiment_id
        if not allow_duplicate:
            if _already_logged(client, experiment_id, view.run_id):
                raise DuplicateRunError(
                    f"run {view.run_id} is already logged to experiment {experiment!r}; "
                    "pass --allow-duplicate to log it again"
                )
    tags = {RUN_ID_TAG: view.run_id}
    if view.dataset_id:
        tags[DATASET_ID_TAG] = view.dataset_id
    run = client.create_run(experiment_id, tags=tags)
    run_id = run.info.run_id
    try:
        for key, value in params_of(view).items():
            client.log_param(run_id, key, value)
        for key, number in sorted(metrics.items()):
            client.log_metric(run_id, key, number)
        for path in (view.path, profile, report):
            if path is not None:
                client.log_artifact(run_id, str(path))
    except BaseException:
        client.set_terminated(run_id, status="FAILED")
        raise
    client.set_terminated(run_id, status="FINISHED")
    return str(run_id)


def _fail(message: str, code: int) -> int:
    print(f"shape: error: {message}", file=sys.stderr)
    return code


class MlflowCommand:
    name = "mlflow"
    help = "Log a run manifest, profile and verify report to MLflow"

    def configure(self, parser: Any) -> None:
        sub = parser.add_subparsers(dest="action", required=True)
        log = sub.add_parser(
            "log", help="log a run manifest to an MLflow experiment", allow_abbrev=False
        )
        log.add_argument("manifest", metavar="MANIFEST.json", help="the run manifest")
        log.add_argument("--profile", metavar="P.shape", help="a profile to attach")
        log.add_argument(
            "--verify-report",
            metavar="R.json",
            help="a `shape verify` JSON report: its gates become metrics and it is attached",
        )
        log.add_argument(
            "--experiment", default=DEFAULT_EXPERIMENT, help="experiment name (default: shape)"
        )
        log.add_argument("--tracking-uri", metavar="URI", help="MLflow tracking URI")
        log.add_argument(
            "--allow-duplicate",
            action="store_true",
            help="log the run even when its run id is already in the experiment",
        )

    def run(self, args: Any) -> int:
        try:
            if not str(args.experiment).strip():
                raise ManifestError("--experiment is empty")
            view, profile, report, metrics = _read_inputs(args)
            mlflow_run = log_run(
                view,
                profile=profile,
                report=report,
                metrics=metrics,
                experiment=args.experiment,
                tracking_uri=args.tracking_uri,
                allow_duplicate=args.allow_duplicate,
            )
        except MissingExtraError as exc:
            return _fail(str(exc), EXIT_INPUT)
        except (ManifestError, ReportError) as exc:
            return _fail(str(exc), EXIT_INPUT)
        except DuplicateRunError as exc:
            return _fail(str(exc), EXIT_FAILED)
        except Exception as exc:  # MLflow's own errors: a store that cannot be reached, a conflict
            return _fail(f"MLflow failed: {type(exc).__name__}: {exc}", EXIT_FAILED)
        print(f"logged run {view.run_id} to MLflow run {mlflow_run}")
        return EXIT_OK
