"""``StreamingDemoMode``: generate a small dataset, then stream one table's events.

The first table (in dependency order) is streamed as JSON lines on the run's output, in
event-time order, up to ``max_events`` events; nothing is written to a target, so there is
nothing for a cleanup to remove.
"""

from __future__ import annotations

import logging
from typing import Any

from shape.demo.catalog import ScenarioMeta
from shape.demo.connections import ConnectionProfile
from shape.demo.dashboard import DemoStep, ProgressDashboard
from shape.demo.errors import DemoError, is_expected
from shape.demo.manifest import DemoManifest
from shape.demo.modes.common import check_scale, load_schema, resolve_domains
from shape.demo.params import DemoParams
from shape.demo.runtime import DemoRuntime

logger = logging.getLogger(__name__)


class _LineSink:
    """An event sink that writes each event as one JSON line to a text stream."""

    def __init__(self, out: Any) -> None:
        self._out = out

    def send(self, batch: Any) -> None:
        from shape.streaming.emit.formats import encode_batch

        self._out.write(encode_batch(batch, "flat", "shape").decode("utf-8"))

    def flush(self) -> None:
        self._out.flush()

    def close(self) -> None:
        self.flush()


class StreamingDemoMode:
    def __init__(
        self,
        params: DemoParams,
        manifest: DemoManifest,
        dashboard: ProgressDashboard,
        connection_profile: ConnectionProfile | None,
        meta: ScenarioMeta,
        runtime: DemoRuntime,
    ) -> None:
        self._params = params
        self._manifest = manifest
        self._dashboard = dashboard
        self._conn = connection_profile
        self._meta = meta
        self._rt = runtime
        domains = resolve_domains(params, meta)
        if len(domains) != 1:
            raise DemoError(
                f"streaming streams one domain; got {len(domains)} domains ({', '.join(domains)})"
            )
        self._domain = domains[0]

    def run(self) -> dict[str, Any]:
        import sys

        from shape.generation.engine import Engine
        from shape.streaming.emit.runtime import EmitConfig, EmitRunner
        from shape.streaming.emit.source import EventPlan

        params, dashboard = self._params, self._dashboard
        if params.estimate_only or params.dry_run:
            from shape.demo.estimator import CostEstimator

            print(f"\nCost estimate for {params.scenario}:", file=self._rt.out)
            print(str(CostEstimator().estimate(params.rows, ["generated"])), file=self._rt.out)
            if params.estimate_only:
                return {"success": True, "estimate_only": True}
            print(
                f"[dry-run] Would stream up to {params.max_events:,} events of {self._domain}",
                file=self._rt.out,
            )
            return {"success": True, "dry_run": True}

        dashboard.start()
        try:
            schema = load_schema(self._domain)
            check_scale(schema, "small", self._domain)
            engine = Engine(schema, scale="small", seed=params.seed)
            dashboard.step(DemoStep.GENERATING, "Seeding small scale data")
            for name in engine.order:
                self._manifest.add_artifact("generated", name, engine.row_counts.get(name, 0))
            table = engine.order[0]
            dashboard.step(DemoStep.WRITING, f"Streaming {table} (Ctrl+C to stop)")
            plan = EventPlan(engine, tables=[table], by_event_time=True)
            runner = EmitRunner(
                plan,
                _LineSink(self._rt.out or sys.stdout),
                EmitConfig(max_events=params.max_events),
            )
            try:
                report = runner.run()
            except KeyboardInterrupt:
                runner.request_stop()
                dashboard.info("Interrupted by user")
                dashboard.finish(True)
                return {"success": True, "stopped_by_user": True}
            self._manifest.metrics["events_streamed"] = report.events
            dashboard.step(DemoStep.DONE, f"{report.events:,} events of {table}")
            dashboard.finish(True)
            return {"success": True, "events": report.events}
        except Exception as exc:
            from shape.security.redact import redact_text

            message = redact_text(" ".join(str(exc).split()) or type(exc).__name__)
            if not is_expected(exc):
                logger.exception("streaming demo failed")
            dashboard.finish(False, message)
            return {"success": False, "error": message}
