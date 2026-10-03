"""Run a scenario pack end to end: generate, apply chaos, write the files or events, check the
gates and write the run manifest (P6-14).

Steps: validate the pack against the domain; generate the domain at the scale and seed; apply the
pack's (or the spec's) chaos to the generated tables; simulate by kind (``file_drop`` writes the
listed entities as files, ``stream`` writes each topic's table as JSON Lines events, ``hybrid``
does both); run the validation gates over the final tables; write ``<run_id>_manifest.json``.

Timing fields of a pack (cadence, partitioning, rates, lateness, replay, done flags) describe the
landing pattern for a consumer; this runner does not simulate a clock, and says so in
``docs/SCENARIO_PACKS.md``.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shape.scenario.loader import ScenarioPack
from shape.scenario.manifest import ManifestBuilder, RunManifest
from shape.scenario.validator import (
    KNOWN_GATES,
    PackValidator,
    chaos_config,
    domain_name_of,
    match_table,
    schema_of,
)

if TYPE_CHECKING:
    import pyarrow as pa  # type: ignore[import-untyped]

    from shape.generation.engine import GenerationResult
    from shape.scenario.gsl import GenerationSpec

_FILE_FORMATS = {"parquet": "parquet", "csv": "csv", "jsonl": "jsonl", "json": "jsonl"}


@dataclass
class RunResult:
    """The outcome of a pack run. ``manifest`` is None when the pack failed validation."""

    manifest: RunManifest | None
    files_written: list[str] = field(default_factory=list)
    events_emitted: int = 0
    validation_results: dict[str, bool] = field(default_factory=dict)
    gate_messages: dict[str, str] = field(default_factory=dict)
    elapsed_time: float = 0.0
    pack_id: str = ""
    domain: str = ""
    scale: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    chaos_applied: bool = False

    @property
    def is_success(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        lines = [
            f"Pack Run: {'SUCCESS' if self.is_success else 'FAILED'}",
            f"  Pack:    {self.pack_id}",
            f"  Domain:  {self.domain}",
            f"  Scale:   {self.scale}",
            f"  Elapsed: {self.elapsed_time:.1f}s",
            f"  Files:   {len(self.files_written)}",
            f"  Events:  {self.events_emitted:,}",
        ]
        if self.validation_results:
            lines.append("  Validation gates:")
            for gate, passed in self.validation_results.items():
                detail = "" if passed else f" ({self.gate_messages.get(gate, 'failed')})"
                lines.append(f"    {gate}: {'PASS' if passed else 'FAIL'}{detail}")
        if self.chaos_applied and self.manifest is not None and self.manifest.chaos:
            lines.append(
                "  Chaos:   "
                + ", ".join(f"{k} {v}" for k, v in sorted(self.manifest.chaos.items()))
            )
        if self.errors:
            lines.append(f"  Errors ({len(self.errors)}):")
            lines.extend(f"    {e}" for e in self.errors)
        return "\n".join(lines)


class PackRunner:
    """Execute a :class:`ScenarioPack` against a domain."""

    def run(
        self,
        pack: ScenarioPack,
        domain: Any,
        scale: str = "small",
        seed: int = 42,
        base_path: str | Path = ".",
        *,
        spec: GenerationSpec | None = None,
    ) -> RunResult:
        """Run ``pack`` on ``domain`` (a loaded domain or a generation schema) into ``base_path``.

        A generation ``spec`` overrides the pack's gates, chaos, landing root and entity list
        where it sets them, and its file is hashed into the manifest.
        """
        started = time.perf_counter()
        pack = _with_spec(pack, spec)
        name = domain_name_of(domain)

        def failed(errors: list[str], manifest: RunManifest | None = None) -> RunResult:
            return RunResult(
                manifest=manifest,
                errors=errors,
                pack_id=pack.id,
                domain=name,
                scale=scale,
                elapsed_time=time.perf_counter() - started,
            )

        checked = PackValidator().validate(pack, domain)
        if not checked.is_valid:
            return failed([f"Pack validation failed: {e}" for e in checked.errors])
        schema = schema_of(domain)
        presets = schema.generation.scales
        if presets and scale not in presets:
            return failed([f"unknown scale {scale!r}; the presets are: {', '.join(presets)}"])
        output_root = Path(base_path)
        output_root.mkdir(parents=True, exist_ok=True)

        builder = ManifestBuilder()
        builder.start(spec.path if spec is not None else None, pack, name, scale, seed)
        try:
            from shape.generation.engine import Engine

            generated = Engine(schema, scale=scale, seed=seed).generate()
        except Exception as exc:
            return failed([f"Data generation failed: {exc}"], builder.finish())

        chaos_applied = False
        section = _chaos_section(pack, spec)
        if section is not None:
            generated = _apply_chaos(generated, section, builder)
            chaos_applied = True

        builder.record_dataset(generated.tables)
        errors: list[str] = []
        files: list[str] = []
        table_files: dict[str, list[str]] = {t: [] for t in generated.tables}
        events = 0
        try:
            if pack.kind == "file_drop":
                files = self._file_drop(pack, generated, output_root, table_files)
            elif pack.kind == "stream":
                events = self._stream(pack, generated, output_root, table_files, files)
            elif pack.kind == "hybrid":
                events = self._hybrid(pack, generated, output_root, table_files, files)
        except OSError as exc:
            errors.append(f"Writing the output failed: {exc}")

        for table_name, table in generated.tables.items():
            builder.record_output(
                table_name, table.num_rows, table.num_columns, table_files[table_name]
            )
        builder.record_outputs(
            {"kind": pack.kind, "files": len(files), "events": events, "root": str(output_root)}
        )

        results: dict[str, bool] = {}
        messages: dict[str, str] = {}
        gates = pack.validation.required_gates if pack.validation else []
        for gate in gates:
            passed, message = _run_gate(gate, generated)
            results[gate] = passed
            builder.record_validation(gate, passed)
            if not passed:
                messages[gate] = message
                if not chaos_applied:  # with chaos, a failing gate is what the run set out to show
                    errors.append(f"Validation gate '{gate}' failed: {message}")

        manifest = builder.finish()
        manifest_path = output_root / f"{manifest.run_id}_manifest.json"
        counter = 1
        while manifest_path.exists():  # two runs in one second must not overwrite each other
            counter += 1
            manifest.run_id = f"{manifest.run_id.rsplit('_x', 1)[0]}_x{counter}"
            manifest_path = output_root / f"{manifest.run_id}_manifest.json"
        ManifestBuilder.to_file(manifest, manifest_path)
        files.append(str(manifest_path))
        return RunResult(
            manifest=manifest,
            files_written=files,
            events_emitted=events,
            validation_results=results,
            gate_messages=messages,
            elapsed_time=time.perf_counter() - started,
            pack_id=pack.id,
            domain=name,
            scale=scale,
            errors=errors,
            warnings=checked.warnings,
            chaos_applied=chaos_applied,
        )

    # ---- simulation ------------------------------------------------------------------------

    def _landing(self, pack: ScenarioPack, output_root: Path) -> Path:
        landing = output_root
        root = pack.fabric_targets.get("lakehouse_files_root")
        if root:
            landing = output_root / str(root)
        landing.mkdir(parents=True, exist_ok=True)
        if not landing.resolve().is_relative_to(output_root.resolve()):
            raise OSError(f"landing path {landing} leaves the output directory")
        return landing

    def _file_drop(
        self,
        pack: ScenarioPack,
        generated: GenerationResult,
        output_root: Path,
        table_files: dict[str, list[str]],
    ) -> list[str]:
        if pack.file_drop is None:
            return []
        formats = pack.file_drop.formats
        fmt = _FILE_FORMATS.get(formats[0] if formats else "parquet", "csv")
        landing = self._landing(pack, output_root)
        return _write_tables(generated, pack.file_drop.entities, fmt, landing, table_files)

    def _stream(
        self,
        pack: ScenarioPack,
        generated: GenerationResult,
        output_root: Path,
        table_files: dict[str, list[str]],
        files: list[str],
    ) -> int:
        if pack.streaming is None:
            return 0
        return _write_topics(pack.streaming.topics, generated, output_root, "", table_files, files)

    def _hybrid(
        self,
        pack: ScenarioPack,
        generated: GenerationResult,
        output_root: Path,
        table_files: dict[str, list[str]],
        files: list[str],
    ) -> int:
        if pack.hybrid is None:
            return 0
        events = 0
        batch = pack.hybrid.micro_batch
        if batch is not None:
            micro_root = output_root / "micro_batch"
            micro_root.mkdir(parents=True, exist_ok=True)
            fmt = _FILE_FORMATS.get(batch.formats[0] if batch.formats else "jsonl", "csv")
            files.extend(_write_tables(generated, batch.entities, fmt, micro_root, table_files))
        if pack.hybrid.stream is not None and pack.hybrid.stream.topics:
            events = _write_topics(
                pack.hybrid.stream.topics, generated, output_root, "stream_", table_files, files
            )
        return events


# ---- spec overrides -----------------------------------------------------------------------


def _with_spec(pack: ScenarioPack, spec: GenerationSpec | None) -> ScenarioPack:
    """``pack`` with the settings a generation spec overrides (a copy; the pack is not changed)."""
    if spec is None:
        return pack
    out = replace(pack)
    if spec.validation is not None and spec.validation.gates:
        out.validation = replace(
            out.validation or _empty_validation(), required_gates=list(spec.validation.gates)
        )
    lake = spec.outputs.lakehouse if spec.outputs else None
    if lake is not None:
        if lake.landing_zone is not None and lake.landing_zone.root:
            out.fabric_targets = {
                **out.fabric_targets,
                "lakehouse_files_root": lake.landing_zone.root,
            }
        if lake.tables and out.file_drop is not None:
            out.file_drop = replace(out.file_drop, entities=list(lake.tables))
    return out


def _empty_validation() -> Any:
    from shape.scenario.loader import ValidationSpec

    return ValidationSpec()


def _chaos_section(pack: ScenarioPack, spec: GenerationSpec | None) -> dict[str, Any] | None:
    """The chaos settings that apply: the spec's when it enables chaos, else the pack's."""
    if spec is not None and spec.chaos is not None and spec.chaos.enabled:
        return {**spec.chaos.config, "enabled": True, "intensity": spec.chaos.intensity}
    if pack.chaos is not None and pack.chaos.get("enabled", False):
        return dict(pack.chaos)
    return None


def _apply_chaos(
    generated: GenerationResult, section: dict[str, Any], builder: ManifestBuilder
) -> GenerationResult:
    """Run the chaos engine once, on the run's day, over every generated table; count what it
    changed per category in the manifest. The run's day is ``chaos.day``, else the later of the
    chaos start and the breaking-change day (so every category can fire)."""
    from shape.chaos import ChaosCategory, ChaosEngine

    config = chaos_config(section)
    day = int(section.get("day", max(config.chaos_start_day, config.breaking_change_day)))
    engine = ChaosEngine(config)
    tables = dict(generated.tables)
    for name in generated.generation_order:
        table = tables[name]
        if engine.should_inject(day, ChaosCategory.SCHEMA.value):
            table = engine.drift_schema(table, day)
            builder.record_chaos("schema", sum(e.rows or 1 for e in engine.last_events))
        if engine.should_inject(day, ChaosCategory.VALUE.value):
            table = engine.corrupt_values(table, day)
            builder.record_chaos("value", sum(e.rows for e in engine.last_events))
        if engine.should_inject(day, ChaosCategory.TEMPORAL.value):
            from shape.chaos.categories import _is_datetime, column_indices

            cols = [table.schema.field(i).name for i in column_indices(table, _is_datetime)]
            if cols:
                table = engine.inject_temporal_chaos(table, cols, day)
                builder.record_chaos("temporal", sum(e.rows for e in engine.last_events))
        if engine.should_inject(day, ChaosCategory.VOLUME.value):
            table = engine.inject_volume_chaos(table, day)
            builder.record_chaos("volume", sum(e.rows for e in engine.last_events))
        tables[name] = table
        if engine.should_inject(day, ChaosCategory.REFERENTIAL.value):
            tables = engine.inject_referential_chaos(tables, day)
            builder.record_chaos("referential", sum(e.rows for e in engine.last_events))
    return replace(
        generated,
        tables=tables,
        row_counts={n: t.num_rows for n, t in tables.items()},
    )


# ---- writers ------------------------------------------------------------------------------


def _write_tables(
    generated: GenerationResult,
    entities: list[str],
    fmt: str,
    directory: Path,
    table_files: dict[str, list[str]],
) -> list[str]:
    """Write the ``entities`` (every table when none are listed) as ``fmt`` into ``directory``."""
    from shape.generation.output import write_result

    names = [n for n in generated.generation_order if not entities or n in entities]
    subset = replace(
        generated,
        tables={n: generated.tables[n] for n in names},
        generation_order=names,
        row_counts={n: generated.tables[n].num_rows for n in names},
    )
    paths = write_result(subset, fmt, directory)
    for name, path in zip(names, paths, strict=True):
        table_files[name].append(str(path))
    return [str(p) for p in paths]


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _write_jsonl(table: pa.Table, path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for batch in table.to_batches():
            for row in batch.to_pylist():
                fh.write(json.dumps(row, default=_json_default, ensure_ascii=False))
                fh.write("\n")


def _write_topics(
    topics: list[Any],
    generated: GenerationResult,
    output_root: Path,
    prefix: str,
    table_files: dict[str, list[str]],
    files: list[str],
) -> int:
    """One JSON Lines file per topic (``<prefix><topic>_<event_type>.jsonl``) holding the rows of
    the table the topic stands for; returns the number of events."""
    events = 0
    tables = list(generated.generation_order)
    for topic in topics:
        table_name = match_table(topic.name, tables)
        if table_name is None:
            continue
        table = generated.tables[table_name]
        events += table.num_rows
        target = output_root / f"{prefix}{topic.name}_{topic.event_type}.jsonl"
        _write_jsonl(table, target)
        files.append(str(target))
        table_files[table_name].append(str(target))
    return events


# ---- validation gates ---------------------------------------------------------------------


def _run_gate(gate: str, generated: GenerationResult) -> tuple[bool, str]:
    """Run one gate over the final tables: ``(passed, why not)``. A gate Shape does not know
    fails (it never passes by default)."""
    schema = generated.schema
    tables = generated.tables
    if gate == "referential_integrity":
        problems = _key_type_problems(generated) or generated.verify_integrity()
        return (not problems, "; ".join(problems[:3]))
    if gate == "schema_conformance":
        for name, tdef in schema.tables.items():
            if name not in tables:
                return False, f"table {name} was not generated"
            missing = [c for c in tdef.columns if c not in tables[name].column_names]
            if missing:
                return False, f"{name} lacks columns {', '.join(missing)}"
        return True, ""
    if gate == "row_count":
        empty = [n for n, t in tables.items() if t.num_rows == 0]
        return (not empty, f"empty tables: {', '.join(empty)}" if empty else "")
    if gate == "null_check":
        for name, tdef in schema.tables.items():
            if name not in tables:
                continue
            for cname, col in tdef.columns.items():
                if col.nullable or col.null_rate > 0 or cname not in tables[name].column_names:
                    continue
                if tables[name].column(cname).null_count:
                    return False, f"{name}.{cname} has nulls"
        return True, ""
    if gate == "uniqueness":
        for name, tdef in schema.tables.items():
            if name not in tables:
                continue
            keys = [c for c in tdef.primary_key if c in tables[name].column_names]
            if keys and _has_duplicates(tables[name], keys):
                return False, f"{name} has duplicate primary keys"
        return True, ""
    return False, f"unknown gate (known: {', '.join(sorted(KNOWN_GATES))})"


def _key_type_problems(generated: GenerationResult) -> list[str]:
    """The relationships whose child key cannot be compared with its parent key (chaos can
    retype a key to text): that is an integrity failure, not a crash."""
    import pyarrow as pa  # type: ignore[import-untyped]
    import pyarrow.compute as pc  # type: ignore[import-untyped]

    problems: list[str] = []
    tables = generated.tables
    for rel in generated.schema.relationships:
        if rel.type == "self_referencing" or rel.parent not in tables or rel.child not in tables:
            continue
        parent, child = tables[rel.parent], tables[rel.child]
        for p_col, c_col in zip(rel.parent_columns, rel.child_columns, strict=False):
            if p_col not in parent.column_names or c_col not in child.column_names:
                continue
            try:  # the same comparison verify_integrity makes, on no rows
                pc.is_in(
                    child[c_col].slice(0, 0), value_set=parent[p_col].combine_chunks().slice(0, 0)
                )
            except (pa.ArrowException, TypeError):
                p_type, c_type = parent.schema.field(p_col).type, child.schema.field(c_col).type
                problems.append(
                    f"{rel.child}.{c_col} ({c_type}) cannot be compared with "
                    f"{rel.parent}.{p_col} ({p_type})"
                )
    return problems


def _has_duplicates(table: pa.Table, keys: list[str]) -> bool:
    return bool(table.select(keys).group_by(keys).aggregate([]).num_rows != table.num_rows)
