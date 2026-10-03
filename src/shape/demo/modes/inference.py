"""``InferenceDemoMode``: learn from data, generate synthetic data, compare."""

from __future__ import annotations

import importlib
import logging
import warnings
from pathlib import Path
from typing import Any

from shape.demo.catalog import ScenarioMeta
from shape.demo.connections import ConnectionProfile
from shape.demo.dashboard import DemoStep, ProgressDashboard
from shape.demo.errors import DemoError, is_expected
from shape.demo.fidelity import FidelityReport
from shape.demo.manifest import DemoManifest
from shape.demo.modes.common import (
    check_scale,
    domain_label,
    load_schema,
    resolve_domains,
    rows_to_scale,
)
from shape.demo.params import DemoParams
from shape.demo.runtime import DemoRuntime

logger = logging.getLogger(__name__)

FILE_SUFFIXES = (".csv", ".parquet", ".pq", ".jsonl", ".ndjson")


class InferenceDemoMode:
    """Profile source data, generate a synthetic dataset from what was learned, and compare."""

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
                "inference learns from one domain or one data source; "
                f"got {len(domains)} domains ({', '.join(domains)})"
            )
        self._domain = domains[0]

    def run(self) -> dict[str, Any]:
        from shape.generation.engine import Engine
        from shape.generation.learn import as_dataset, learn
        from shape.profile.reference import profile

        dashboard = self._dashboard
        params = self._params
        if params.estimate_only or params.dry_run:
            return self._plan()
        dashboard.start()
        try:
            dashboard.step(DemoStep.PROFILING, self._describe_input())
            real_profile = as_dataset(self._profile_source())
            dashboard.info(f"Profiled {len(real_profile.tables)} table(s)")

            schema = learn(real_profile, domain_label(self._domain))
            dashboard.info(f"Built schema: {len(schema.tables)} tables")

            scale = rows_to_scale(params.rows)
            check_scale(schema, scale, self._domain)
            dashboard.step(DemoStep.GENERATING, f"{params.rows:,} rows (approx)")
            tables = Engine(schema, scale=scale, seed=params.seed).generate().tables
            total = sum(t.num_rows for t in tables.values())
            dashboard.info(f"Generated {total:,} total rows")

            synthetic = as_dataset(profile(dict(tables)))

            dashboard.step(DemoStep.COMPARING)
            report = FidelityReport(real_profile, synthetic, out=self._rt.out)
            score = report.score_or_none()
            dashboard.info(f"Fidelity score: {report.score_text()}")

            self._render_output(report, real_profile, synthetic, score, schema)

            self._manifest.metrics["fidelity_score"] = None if score is None else round(score, 4)
            self._manifest.metrics["tables_profiled"] = len(real_profile.tables)
            for tname, table in tables.items():
                self._manifest.add_artifact("synthetic", tname, row_count=table.num_rows)

            dashboard.step(DemoStep.DONE)
            dashboard.finish(True)
            return {
                "success": True,
                "fidelity_score": score,
                "generated_data": tables,
                "schema": schema,
            }
        except Exception as exc:
            from shape.security.redact import redact_text

            message = redact_text(" ".join(str(exc).split()) or type(exc).__name__)
            if not is_expected(exc):
                logger.exception("inference demo failed")
            dashboard.step(DemoStep.FAILED, message)
            dashboard.finish(False, message)
            return {"success": False, "error": message}

    # ---- plan -------------------------------------------------------------------------------

    def _plan(self) -> dict[str, Any]:
        from shape.demo.estimator import CostEstimator

        p = self._params
        print(f"\nCost estimate for {p.scenario} ({p.rows:,} rows):", file=self._rt.out)
        print(str(CostEstimator().estimate(p.rows, ["generated"])), file=self._rt.out)
        if p.estimate_only:
            return {"success": True, "estimate_only": True}
        print(
            f"[dry-run] Would profile {self._describe_input()}, learn a schema and generate "
            f"{rows_to_scale(p.rows)} scale data, then compare",
            file=self._rt.out,
        )
        return {"success": True, "dry_run": True}

    # ---- the source -------------------------------------------------------------------------

    def _describe_input(self) -> str:
        if self._params.input_file == "live-db":
            return f"live DB via {self._params.connection or 'default'}"
        if self._params.input_file:
            return str(self._params.input_file)
        return "domain defaults"

    def _profile_source(self) -> Any:
        if self._params.input_file == "live-db":
            return self._profile_live_db()
        if self._params.input_file:
            return self._profile_file(self._params.input_file)
        return self._profile_domain_defaults()

    def _profile_live_db(self) -> Any:
        if self._conn is None:
            raise DemoError("live-db mode requires a connection profile. Use --connection.")
        conn_str = self._conn.warehouse_conn_str or self._conn.sql_db_conn_str
        if not conn_str:
            raise DemoError("the connection profile has no warehouse or SQL database to profile")
        from shape.scale.sinks.fabric import connection_and_auth

        resolved, _ = connection_and_auth(conn_str, None, True)
        schema, sample = self._params.db_schema, self._params.sample_rows
        tables = self._params.db_tables
        injected = self._rt.profile_database
        if injected is not None:
            return injected(resolved, schema=schema, sample_rows=sample, tables=tables)
        try:
            plugin = importlib.import_module("shape_sqlserver")
        except ImportError as exc:
            raise DemoError(
                "profiling a database needs the shape-sqlserver plugin: "
                "pip install 'sqllocks-shape[sqlserver]'"
            ) from exc
        secret = self._conn.client_secret
        if secret:
            from shape.security import credrefs

            secret = credrefs.resolve_reference(secret)
        credentials = plugin.Credentials(
            self._conn.auth_method,
            self._conn.tenant_id or None,
            self._conn.client_id or None,
            secret or None,
        )
        return plugin.profile_database(
            resolved, credentials=credentials, schema=schema, sample_rows=sample, tables=tables
        )

    @staticmethod
    def _profile_file(path: str) -> Any:
        from shape.profile.reference import profile

        p = Path(path)
        if p.suffix.lower() not in FILE_SUFFIXES:
            raise DemoError(
                f"unsupported file type: {p.suffix or p.name!r}. Use .csv, .parquet or .jsonl."
            )
        if not p.is_file():
            raise DemoError(f"file not found: {path}")
        return profile(p)

    def _profile_domain_defaults(self) -> Any:
        """A small reference dataset generated from the domain, then profiled."""
        from shape.generation.engine import Engine
        from shape.profile.reference import profile

        schema = load_schema(self._domain)
        check_scale(schema, "small", self._domain)
        tables = Engine(schema, scale="small", seed=self._params.seed).generate().tables
        return profile(dict(tables))

    # ---- output -----------------------------------------------------------------------------

    def _render_output(
        self, report: FidelityReport, real: Any, synthetic: Any, score: float | None, schema: Any
    ) -> None:
        formats = self._params.output_formats or ["terminal"]
        explicit = "all" not in formats
        if "all" in formats:
            formats = ["terminal", "charts", "semantic_model"]
        if "terminal" in formats:
            report.render()
        if "charts" in formats:
            from shape.demo.charts import render_charts

            render_charts(
                real,
                synthetic,
                score,
                self._output_dir(),
                self._params.scenario,
                self._manifest,
                self._rt.out,
            )
        if "semantic_model" in formats:
            from shape.demo.charts import write_semantic_model

            write_semantic_model(
                schema,
                self._output_dir(),
                self._params.scenario,
                self._manifest,
                self._rt.out,
                required=explicit,
            )

    def _output_dir(self) -> Path:
        out = Path(self._params.output_dir or ".").resolve()
        out.mkdir(parents=True, exist_ok=True)
        self._manifest.params["output_dir"] = str(out)
        return out


warnings.filterwarnings("ignore", category=RuntimeWarning, module="scipy")
