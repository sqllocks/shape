"""``SeedingDemoMode``: generate a scenario and write it to the targets of a connection profile.

Targets: a folder on this machine (Parquet part files), a Lakehouse, a Warehouse, a SQL database
and an Eventhouse; with no connection profile the rows are generated and counted, and nothing is
written. A table is written in the default write mode, which refuses a table that already exists:
a demo never overwrites your data, and the manifest lists only tables the destination accepted,
so a cleanup removes nothing but what the demo made.

Generation runs through the scale router (``local_single`` below 500,000 rows, ``local_mp`` from
there) or, with ``--scale-mode spark``, is submitted to a Fabric Spark notebook that writes Delta
tables into the Lakehouse.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from shape.demo.catalog import ScenarioMeta
from shape.demo.cleanup import mark_session_folder
from shape.demo.connections import ConnectionProfile
from shape.demo.dashboard import DemoStep, ProgressDashboard
from shape.demo.errors import DemoError, is_expected
from shape.demo.estimator import CostEstimator
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
from shape.scale.sinks.base import BaseSink, Sink

logger = logging.getLogger(__name__)

SPARK_AUTO_THRESHOLD = 500_000
CHUNK_ROWS = 500_000
DEFAULT_SEED = 42


def resolve_scale_mode(requested: str, conn: ConnectionProfile | None, rows: int) -> str:
    """``auto`` becomes ``spark`` when a connection with a Lakehouse is given and the rows reach
    500,000, else ``local``. ``spark`` needs a connection profile with a Lakehouse."""
    if requested == "local":
        return "local"
    if requested == "spark":
        if conn is None:
            raise ValueError("Spark mode requires a connection profile")
        if not conn.lakehouse_id:
            raise ValueError("Spark mode requires lakehouse_id in connection profile")
        return "spark"
    if conn is not None and conn.lakehouse_id and rows >= SPARK_AUTO_THRESHOLD:
        return "spark"
    return "local"


class _Counting(BaseSink):
    """The sink of a run with no target: it keeps nothing (the router counts the rows)."""

    name = "generated"

    def write_batch(self, table: str, batch: Any) -> None:
        return None


class _Planned:
    """One sink of a run: its target kind, the sink, and where its tables go."""

    def __init__(self, target: str, sink: Sink, **where: str) -> None:
        self.target = target
        self.sink = sink
        self.where = where


class SeedingDemoMode:
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
        self._domains = resolve_domains(params, meta)

    # ---- the run ----------------------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        params, dashboard = self._params, self._dashboard
        try:
            scale_mode = resolve_scale_mode(params.scale_mode, self._conn, params.rows)
        except ValueError as exc:
            self._manifest.scale_mode = params.scale_mode
            return {"success": False, "error": str(exc)}
        self._manifest.scale_mode = scale_mode
        targets = self._available_targets()

        if params.estimate_only or params.dry_run:
            estimate = CostEstimator().estimate(params.rows, targets)
            print(
                f"\nCost estimate for {params.scenario} ({params.rows:,} rows):", file=self._rt.out
            )
            print(str(estimate), file=self._rt.out)
            if params.estimate_only:
                return {"success": True, "estimate_only": True}
            print(
                f"[dry-run] Would write {params.rows:,} rows to: "
                f"{', '.join(targets)} via scale_mode={scale_mode}",
                file=self._rt.out,
            )
            return {"success": True, "dry_run": True}

        dashboard.start()
        dashboard.step(DemoStep.GENERATING, f"{params.rows:,} rows ({scale_mode})")
        try:
            stats = self._run_local(targets) if scale_mode == "local" else self._run_spark()
        except Exception as exc:
            from shape.security.redact import redact_text

            message = redact_text(" ".join(str(exc).split()) or type(exc).__name__)
            if not is_expected(exc):
                logger.exception("seeding failed")
            dashboard.finish(False, message)
            return {"success": False, "error": message}
        dashboard.step(DemoStep.DONE)
        dashboard.finish(True)
        self._manifest.metrics.update(stats.get("metrics", {}))
        result: dict[str, Any] = {"success": True, "session_id": self._manifest.session_id}
        result.update(stats.get("result", {}))
        return result

    def _available_targets(self) -> list[str]:
        conn = self._conn
        if conn is None:
            return ["generated"]
        if conn.warehouse_conn_str and not conn.warehouse_staging_path:
            raise DemoError(
                "the connection profile has a warehouse but no warehouse_staging_path "
                "(an abfss:// or onelake:// folder the load reads from)"
            )
        if conn.eventhouse_uri and not conn.eventhouse_database:
            raise DemoError("the connection profile has an Eventhouse URI but no database")
        if conn.lakehouse_id and not conn.workspace_id:
            raise DemoError("the connection profile has a lakehouse_id but no workspace_id")
        return conn.targets() or ["generated"]

    # ---- local ------------------------------------------------------------------------------

    def _session_dir(self) -> Path | None:
        if self._conn is None or not self._conn.local_path:
            return None
        folder = Path(self._conn.local_path).expanduser() / self._manifest.session_id
        mark_session_folder(folder, self._manifest.session_id)
        return folder

    def _make_sink(self, name: str, settings: Mapping[str, Any]) -> Sink:
        if self._rt.sink_factory is not None:
            made = self._rt.sink_factory(name, settings)
            if made is not None:
                return made  # type: ignore[no-any-return]
        from shape.scale.sinks import build_sink

        auth = self._conn.auth_settings() if self._conn is not None else None
        return build_sink(name, settings, chunk_rows=CHUNK_ROWS, auth=auth)

    def _plan_sinks(
        self, targets: list[str], domain: str, multi: bool, session_dir: Path | None
    ) -> list[_Planned]:
        conn, sid = self._conn, self._manifest.session_id
        label = domain_label(domain)
        if conn is None:
            return [_Planned("generated", _Counting())]
        planned: list[_Planned] = []
        schema_name = label if multi else "dbo"
        for target in targets:
            if target == "local":
                assert session_dir is not None
                folder = session_dir / label if multi else session_dir
                planned.append(
                    _Planned(
                        "file",
                        self._make_sink("parquet", {"output_dir": str(folder)}),
                        folder=str(folder),
                    )
                )
            elif target == "lakehouse":
                from shape.demo.services import onelake_base

                base = f"{onelake_base(conn)}/shape_demo/{sid}" + (f"/{label}" if multi else "")
                planned.append(
                    _Planned(
                        "lakehouse",
                        self._make_sink("lakehouse", {"base_path": base, "format": "parquet"}),
                        base=base,
                    )
                )
            elif target == "warehouse":
                planned.append(
                    _Planned(
                        "warehouse",
                        self._make_sink(
                            "warehouse",
                            {
                                "connection_string": conn.warehouse_conn_str,
                                "staging_path": conn.warehouse_staging_path,
                                "schema_name": schema_name,
                                "write_mode": "create",
                            },
                        ),
                        schema=schema_name,
                    )
                )
            elif target == "sql_db":
                planned.append(
                    _Planned(
                        "sql_db",
                        self._make_sink(
                            "sql_database",
                            {
                                "connection_string": conn.sql_db_conn_str,
                                "schema_name": schema_name,
                                "write_mode": "create",
                            },
                        ),
                        schema=schema_name,
                    )
                )
            elif target == "eventhouse":
                prefix = f"{label}_" if multi else ""
                planned.append(
                    _Planned(
                        "eventhouse",
                        self._make_sink(
                            "kql",
                            {
                                "cluster_uri": conn.eventhouse_uri,
                                "database": conn.eventhouse_database,
                                "table_prefix": prefix,
                                "write_mode": "create",
                            },
                        ),
                        prefix=prefix,
                    )
                )
        return planned

    def _record(self, planned: list[_Planned], tables: Mapping[str, int]) -> None:
        """Record what each sink wrote. A sink that reports its own per-table rows
        (``rows_written``) is believed over the router's totals, so a table the destination did
        not accept is never listed."""
        for item in planned:
            written = getattr(item.sink, "rows_written", None)
            counts = dict(written) if isinstance(written, dict) else dict(tables)
            for table, rows in counts.items():
                if item.target == "file":
                    self._manifest.add_artifact(
                        "file", table, rows, str(Path(item.where["folder"]) / table)
                    )
                elif item.target == "lakehouse":
                    self._manifest.add_artifact(
                        "lakehouse", table, rows, f"{item.where['base']}/{table}"
                    )
                elif item.target in ("warehouse", "sql_db"):
                    self._manifest.add_artifact(
                        item.target, table, rows, f"{item.where['schema']}.{table}"
                    )
                elif item.target == "eventhouse":
                    self._manifest.add_artifact("eventhouse", item.where["prefix"] + table, rows)
                else:
                    self._manifest.add_artifact("generated", table, rows)

    def _run_local(self, targets: list[str]) -> dict[str, Any]:
        from shape.generation.engine import Engine
        from shape.scale.router import ScaleRouter

        params = self._params
        seed = params.seed if params.seed is not None else DEFAULT_SEED
        scale = rows_to_scale(params.rows)
        multi = len(self._domains) > 1
        session_dir = self._session_dir()
        # Every domain is loaded before anything is written: a missing one fails the run early.
        schemas = {d: load_schema(d) for d in self._domains}
        for domain, schema in schemas.items():
            check_scale(schema, scale, domain)
        mode = "local_single" if params.rows < SPARK_AUTO_THRESHOLD else "local_mp"
        total = 0
        summary: dict[str, Any] = {}
        for domain, schema in schemas.items():
            engine = Engine(schema, scale=scale, seed=seed, chunk_rows=CHUNK_ROWS)
            planned = self._plan_sinks(targets, domain, multi, session_dir)
            router = ScaleRouter(
                engine, [p.sink for p in planned], mode=mode, chunk_size=CHUNK_ROWS
            )
            try:
                stats = router.run()
            except BaseException:
                # What the sinks completed, and the folder of the files, so a rollback finds it.
                self._record(planned, {})
                if session_dir is not None and not any(
                    a.detail == str(session_dir) for a in self._manifest.artifacts
                ):
                    self._manifest.add_artifact("file", session_dir.name, 0, str(session_dir))
                raise
            self._record(planned, stats.tables)
            total += stats.rows_generated
            summary[domain_label(domain)] = stats.to_dict()
            tables = len(stats.tables)
            self._dashboard.info(
                f"{domain_label(domain)}: {stats.rows_generated:,} rows in {tables} tables"
            )
        return {
            "result": {"stats": summary if multi else next(iter(summary.values()))},
            "metrics": {"rows_generated": total},
        }

    # ---- spark ------------------------------------------------------------------------------

    def _run_spark(self) -> dict[str, Any]:
        from shape.scale.api import scale_generate
        from shape.scale.jobs import Jobs

        conn = self._conn
        assert conn is not None
        if len(self._domains) != 1:
            raise DemoError(
                "spark mode runs one domain; use --scale-mode local for a composite scenario"
            )
        domain = self._domains[0]
        scale = rows_to_scale(self._params.rows)
        schema = load_schema(domain)
        check_scale(schema, scale, domain)
        prefix = self._params.table_prefix or f"shape_{domain_label(domain)}_{scale}_"
        seed = self._params.seed if self._params.seed is not None else DEFAULT_SEED
        others = [t for t in conn.targets() if t not in ("lakehouse", "local")]
        if others:
            print(
                f"     Note: a Spark run writes Delta tables into the Lakehouse only; "
                f"{', '.join(others)} is not written",
                file=self._rt.out,
            )
        request: dict[str, Any] = {
            "domain": domain,
            "scale": scale,
            "seed": seed,
            "scale_mode": "fabric_spark",
            "sinks": ["lakehouse"],
            "fabric": {
                "workspace_id": conn.workspace_id,
                "lakehouse_id": conn.lakehouse_id,
                "table_prefix": prefix,
            },
            "auth": conn.auth_settings(),
        }
        result = scale_generate(
            request,
            jobs=self._rt.jobs or Jobs(),
            token=self._rt.token,
            storage_token=self._rt.storage_token,
            transport=self._rt.transport,
        )
        fabric = result.get("fabric") or {}
        self._manifest.fabric_run_id = fabric.get("fabric_run_id")
        self._manifest.workspace_id = fabric.get("workspace_id") or conn.workspace_id
        self._manifest.notebook_item_id = fabric.get("notebook_item_id")
        from shape.generation.engine import Engine

        engine = Engine(schema, scale=scale, seed=seed)
        for table in engine.order:
            self._manifest.add_artifact(
                "lakehouse",
                f"{prefix}{table}",
                engine.row_counts.get(table, 0),
                f"onelake://{conn.workspace_id}/{conn.lakehouse_id}/Tables/{prefix}{table}",
            )
        return {
            "result": {
                "fabric_run_id": self._manifest.fabric_run_id,
                "job_id": result.get("job_id"),
                "status": "submitted",
                "schema_temp_path": result.get("spec_path"),
            },
            "metrics": {"job_id": result.get("job_id")},
        }
