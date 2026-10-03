"""``DemoOrchestrator``: run one demo scenario, record it, and roll a failed run back."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from shape.demo.catalog import ScenarioCatalog, get_catalog
from shape.demo.cleanup import CleanupEngine
from shape.demo.connections import ConnectionProfile
from shape.demo.dashboard import ProgressDashboard
from shape.demo.errors import ConnectionNotFoundError, DemoError, is_expected
from shape.demo.manifest import DemoManifest
from shape.demo.params import DemoParams
from shape.demo.runtime import DemoRuntime

logger = logging.getLogger(__name__)

# Settings the manifest records, as the baseline did: every one that was given, as text.
_RECORDED = (
    "scenario", "mode", "connection", "input_file", "db_schema", "db_tables", "sample_rows",
    "rows", "domain", "domains", "output_formats", "env_name", "dry_run", "estimate_only",
    "auto_cleanup", "seed", "scale_mode", "table_prefix",
)  # fmt: skip


@dataclass
class DemoResult:
    success: bool
    session_id: str
    scenario: str
    mode: str
    fidelity_score: float | None = None
    error: str | None = None
    manifest: DemoManifest | None = None


class DemoOrchestrator:
    """Coordinate a full demo run with rollback."""

    def __init__(
        self, catalog: ScenarioCatalog | None = None, runtime: DemoRuntime | None = None
    ) -> None:
        self._catalog = catalog or get_catalog()
        self._rt = runtime or DemoRuntime()

    def run(self, params: DemoParams) -> DemoResult:
        params.validate()
        meta = self._catalog.get(params.scenario)
        if params.mode not in meta.supported_modes:
            raise DemoError(
                f"scenario {params.scenario!r} does not support mode {params.mode!r}. "
                f"Supported: {', '.join(meta.supported_modes)}"
            )
        profile: ConnectionProfile | None = None
        if params.connection:
            profile = self._rt.reg().load(params.connection)

        manifest = DemoManifest(scenario=params.scenario, mode=params.mode)
        manifest.params = {
            k: str(getattr(params, k)) for k in _RECORDED if getattr(params, k) is not None
        }
        dashboard = ProgressDashboard(params.scenario, params.mode, params.rows, self._rt.out)
        try:
            data = self._execute(params, manifest, dashboard, profile, meta)
        except (ConnectionNotFoundError, KeyboardInterrupt):
            raise
        except Exception as exc:
            from shape.security.redact import redact_text

            message = redact_text(" ".join(str(exc).split()) or type(exc).__name__)
            if not is_expected(exc):
                logger.exception("demo failed")
            data = {"success": False, "error": message}

        success = bool(data.get("success", True))
        error = data.get("error")
        if not success:
            error = self._rollback(manifest, profile, error)
        manifest.finish(success, error)
        manifest.save(self._rt.manifest_dir)
        return DemoResult(
            success=success,
            session_id=manifest.session_id,
            scenario=params.scenario,
            mode=params.mode,
            fidelity_score=data.get("fidelity_score"),
            error=error,
            manifest=manifest,
        )

    def _execute(
        self,
        params: DemoParams,
        manifest: DemoManifest,
        dashboard: ProgressDashboard,
        profile: ConnectionProfile | None,
        meta: Any,
    ) -> dict[str, Any]:
        handler: Any
        if params.mode == "inference":
            from shape.demo.modes.inference import InferenceDemoMode

            handler = InferenceDemoMode(params, manifest, dashboard, profile, meta, self._rt)
        elif params.mode == "streaming":
            from shape.demo.modes.streaming import StreamingDemoMode

            handler = StreamingDemoMode(params, manifest, dashboard, profile, meta, self._rt)
        else:
            from shape.demo.modes.seeding import SeedingDemoMode

            handler = SeedingDemoMode(params, manifest, dashboard, profile, meta, self._rt)
        result: dict[str, Any] = handler.run()
        return result

    def _rollback(
        self, manifest: DemoManifest, profile: ConnectionProfile | None, error: str | None
    ) -> str | None:
        """Remove what a failed run left behind; the error says how that went."""
        if not manifest.artifacts:
            return error
        try:
            outcome = CleanupEngine(profile, services=self._rt.services).cleanup(manifest)
        except Exception as exc:  # a cleanup that cannot even start
            return f"{error} (rollback failed: {exc})"
        removed = sum(len(v) for v in outcome.removed.values())
        manifest.metrics["rolled_back"] = removed
        note = f"rolled back {removed} artifact(s)"
        if outcome.failed:
            names = ", ".join(f"{f['target']}/{f['name']}" for f in outcome.failed)
            note += f"; could not remove {names}: run `shape demo cleanup {manifest.session_id}`"
        return f"{error} ({note})"
