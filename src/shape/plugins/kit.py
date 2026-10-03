"""Plugin conformance kit (P2-06): one check per plugin API v1 Protocol.

A plugin author calls the check for the Protocol the plugin implements, with a small sample
the plugin can handle, from any test runner::

    from shape.plugins import kit

    def test_my_detector():
        kit.check_detector(MyDetector(), positives=[...], negatives=[...])

Every check raises :class:`ConformanceError` (an ``AssertionError``) naming the first rule
that broke. :func:`check_plugin` picks the check from an entry-point group, and
:func:`check_distribution` loads every ``shape.*`` entry point of an *installed* distribution
through the plugin host and checks each, which is what ``python -m shape.plugins.kit DIST``
runs. The kit has no test-framework dependency.

What it checks is what the host and the Protocols promise: the object satisfies its Protocol,
has a usable ``name``, returns the documented types, behaves deterministically, and does not
modify its inputs. It cannot judge whether a plugin is *correct*; that is the plugin's own
tests.
"""

from __future__ import annotations

import argparse
import copy
import importlib
import json
import math
import re
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, timedelta
from importlib import metadata
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.plugins.api import v1
from shape.plugins.host import PluginHost, api_major

_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")
_UNRELATED_URI = "shape-kit-unrelated://nothing"


class ConformanceError(AssertionError):
    """A plugin broke a rule of plugin API v1."""


def _require(ok: bool, message: str) -> None:
    if not ok:
        raise ConformanceError(message)


def _same(a: Any, b: Any) -> bool:
    return bool(a.equals(b))


# -- shared rules ---------------------------------------------------------------------------


def check_common(obj: Any, group: str) -> None:
    """Rules every plugin shares: it implements the group's Protocol and has a good ``name``."""
    _require(group in v1.GROUPS, f"unknown plugin group {group!r}; known: {sorted(v1.GROUPS)}")
    proto = v1.PROTOCOLS[v1.GROUPS[group]]
    _require(
        isinstance(obj, proto),
        f"{type(obj).__name__} does not implement {proto.__name__} (group {group}); "
        f"see docs/plugins/api-v1.md",
    )
    name = getattr(obj, "name", None)
    _require(isinstance(name, str) and bool(name), "`name` must be a non-empty string")
    _require(
        bool(_NAME.match(str(name))),
        f"`name` {name!r} must be lowercase letters, digits, '_' or '-', starting with a letter",
    )
    schemes = getattr(obj, "schemes", None)
    if "schemes" in getattr(proto, "__annotations__", {}):
        _require(
            isinstance(schemes, Sequence)
            and not isinstance(schemes, str)
            and len(schemes) > 0
            and all(isinstance(s, str) and s for s in schemes),
            "`schemes` must be a non-empty sequence of non-empty strings",
        )


def check_module_api(module: Any) -> str:
    """The plugin module declares ``SHAPE_API`` with the host's major version; return it."""
    if isinstance(module, str):
        module = importlib.import_module(module)
    declared = getattr(module, "SHAPE_API", None)
    _require(
        isinstance(declared, str),
        f"module {module.__name__} must declare SHAPE_API = '1.x' (a string)",
    )
    try:
        major = api_major(str(declared))
    except ValueError as exc:
        raise ConformanceError(str(exc)) from None
    _require(
        major == api_major(v1.SHAPE_API),
        f"module {module.__name__} declares SHAPE_API {declared!r}; this Shape is API "
        f"{v1.SHAPE_API} and majors must match",
    )
    return str(declared)


def _ctx(n_rows: int, *, chunk: int = 0, row_start: int = 0, seed: int = 7) -> v1.GenerationContext:
    return v1.GenerationContext(
        seed=seed,
        table="kit_table",
        column="kit_column",
        chunk=chunk,
        row_start=row_start,
        n_rows=n_rows,
    )


def _check_array(arr: Any, n_rows: int, what: str) -> None:
    _require(
        isinstance(arr, (pa.Array, pa.ChunkedArray)),
        f"{what} must return a pyarrow Array, got {type(arr).__name__}",
    )
    _require(len(arr) == n_rows, f"{what} returned {len(arr)} values for n_rows={n_rows}")


# -- one check per Protocol -----------------------------------------------------------------


def check_source(obj: Any, uri: str, *, unrelated_uri: str = _UNRELATED_URI) -> None:
    """``Source``: opens ``uri``, and only URIs it can read."""
    check_common(obj, "shape.sources")
    _require(bool(obj.can_open(uri)), f"can_open({uri!r}) is False for the sample URI")
    _require(
        obj.can_open(unrelated_uri) is False,
        f"can_open({unrelated_uri!r}) must be False for a URI the source does not handle",
    )
    schema = obj.schema(uri)
    _require(isinstance(schema, pa.Schema), "schema() must return a pyarrow Schema")
    first = list(obj.read(uri))
    _require(
        all(isinstance(b, pa.RecordBatch) for b in first),
        "read() must yield pyarrow RecordBatches",
    )
    for b in first:
        _require(
            b.schema.equals(schema),
            f"a batch schema differs from schema(): {b.schema} vs {schema}",
        )
    second = list(obj.read(uri))
    _require(
        pa.Table.from_batches(first, schema=schema).equals(
            pa.Table.from_batches(second, schema=schema)
        ),
        "read() twice on the same URI gave different data",
    )


def check_sink(
    obj: Any,
    uri: str,
    batches: Iterable[pa.RecordBatch],
    *,
    table: str = "kit_table",
    read_back: Callable[[], pa.Table] | None = None,
) -> None:
    """``Sink``: writes the batches and reports the row count; ``read_back`` (optional)
    returns what landed at the destination so the data can be compared."""
    check_common(obj, "shape.sinks")
    sample = list(batches)
    _require(len(sample) > 0, "sample `batches` must not be empty")
    rows = sum(b.num_rows for b in sample)
    written = obj.write(uri, table, iter(sample))
    _require(
        isinstance(written, int) and not isinstance(written, bool),
        "write() must return the number of rows written (an int)",
    )
    _require(written == rows, f"write() returned {written} but {rows} rows were given")
    if read_back is not None:
        got = read_back()
        want = pa.Table.from_batches(sample)
        _require(
            got.num_rows == want.num_rows and got.column_names == want.column_names,
            "read_back() does not match what was written",
        )


def check_detector(
    obj: Any,
    *,
    positives: Sequence[pa.Array | Sequence[Any]],
    negatives: Sequence[pa.Array | Sequence[Any]] = (),
) -> None:
    """``SemanticDetector``: labels the positive samples, returns ``None`` for the negatives
    and for empty or all-null input, and gives the same answer every time."""
    check_common(obj, "shape.detectors")
    _require(len(positives) > 0, "give at least one positive sample")

    def run(sample: pa.Array | Sequence[Any]) -> v1.Detection | None:
        arr = sample if isinstance(sample, pa.Array) else pa.array(list(sample))
        result = obj.detect(arr, "kit_column")
        _require(
            result is None or isinstance(result, v1.Detection),
            f"detect() must return a Detection or None, got {type(result).__name__}",
        )
        again = obj.detect(arr, "kit_column")
        _require(again == result, "detect() gave different answers for the same input")
        return result  # type: ignore[no-any-return]

    for sample in positives:
        d = run(sample)
        _require(d is not None, "detect() returned None for a positive sample")
        assert d is not None
        _require(
            isinstance(d.label, str) and bool(d.label),
            "Detection.label must be a non-empty string",
        )
        _require(
            isinstance(d.confidence, (int, float)) and 0.0 <= d.confidence <= 1.0,
            f"Detection.confidence must be in [0, 1], got {d.confidence!r}",
        )
    for sample in negatives:
        _require(run(sample) is None, "detect() did not return None for a negative sample")
    _require(
        run(pa.array([], type=pa.string())) is None, "detect() must return None for empty input"
    )
    _require(
        run(pa.array([None, None], type=pa.string())) is None,
        "detect() must return None for all-null input",
    )


def check_fitter(obj: Any, sample: pa.Array | Sequence[float]) -> None:
    """``DistributionFitter``: a ``FitResult`` whose family it declares, with KS in [0, 1]."""
    check_common(obj, "shape.fitters")
    arr = sample if isinstance(sample, pa.Array) else pa.array(list(sample), type=pa.float64())
    _require(
        bool(obj.families) and all(isinstance(f, str) for f in obj.families),
        "`families` must list the family names the fitter can return",
    )
    result = obj.fit(arr)
    _require(result is not None, "fit() returned None for the sample")
    _require(isinstance(result, v1.FitResult), "fit() must return a FitResult or None")
    _require(
        result.family in obj.families,
        f"fit() returned family {result.family!r}, not in families {list(obj.families)}",
    )
    _require(
        all(isinstance(v, (int, float)) and math.isfinite(v) for v in result.params.values()),
        "FitResult.params must map names to finite numbers",
    )
    _require(0.0 <= result.ks <= 1.0, f"FitResult.ks must be in [0, 1], got {result.ks}")
    again = obj.fit(arr)
    _require(again == result, "fit() gave different results for the same sample")


def check_strategy(
    obj: Any,
    spec: Mapping[str, Any],
    *,
    n_rows: int = 64,
    layout_independent: bool = False,
) -> None:
    """``Strategy``: exactly ``n_rows`` values, the same for the same context, and the spec
    left untouched. Pass ``layout_independent=True`` if the values must not depend on how rows
    are split into chunks (then two half chunks must equal one whole)."""
    check_common(obj, "shape.strategies")
    before = copy.deepcopy(dict(spec))
    out = obj.generate(spec, _ctx(n_rows))
    _check_array(out, n_rows, "generate()")
    _require(_same(out, obj.generate(spec, _ctx(n_rows))), "generate() is not deterministic")
    _require(dict(spec) == before, "generate() modified its `spec` argument")
    _require(len(obj.generate(spec, _ctx(0))) == 0, "generate() must handle n_rows=0")
    if layout_independent:
        _layout_independent(lambda ctx: obj.generate(spec, ctx), n_rows, "generate()")


def check_distribution(
    obj: Any,
    params: Mapping[str, float],
    *,
    n_rows: int = 64,
    layout_independent: bool = False,
) -> None:
    """``Distribution``: ``n_rows`` finite numbers, deterministic for a context."""
    check_common(obj, "shape.distributions")
    before = dict(params)
    out = obj.sample(params, _ctx(n_rows))
    _check_array(out, n_rows, "sample()")
    _require(
        pa.types.is_floating(out.type) or pa.types.is_integer(out.type),
        f"sample() must return numbers, got {out.type}",
    )
    values = out.to_pylist()
    _require(
        all(v is None or math.isfinite(v) for v in values),
        "sample() returned non-finite values for the sample parameters",
    )
    _require(_same(out, obj.sample(params, _ctx(n_rows))), "sample() is not deterministic")
    _require(dict(params) == before, "sample() modified its `params` argument")
    if layout_independent:
        _layout_independent(lambda ctx: obj.sample(params, ctx), n_rows, "sample()")


def _layout_independent(
    make: Callable[[v1.GenerationContext], Any], n_rows: int, what: str
) -> None:
    half = n_rows // 2
    whole = make(_ctx(n_rows))
    parts = [
        make(_ctx(half, chunk=0, row_start=0)),
        make(_ctx(n_rows - half, chunk=1, row_start=half)),
    ]
    joined = pa.chunked_array([*_chunks(parts[0]), *_chunks(parts[1])])
    _require(
        pa.chunked_array(_chunks(whole)).equals(joined),
        f"{what} depends on the chunk layout (one chunk != two half chunks)",
    )


def _chunks(arr: Any) -> list[pa.Array]:
    return list(arr.chunks) if isinstance(arr, pa.ChunkedArray) else [arr]


def check_calendar(obj: Any, start: date | None = None, end: date | None = None) -> None:
    """``Calendar``: one non-negative finite float64 factor per day, ``start`` to ``end``
    inclusive (default: calendar year 2024)."""
    check_common(obj, "shape.calendars")
    start = start or date(2024, 1, 1)
    end = end or date(2024, 12, 31)
    days = (end - start).days + 1
    out = obj.lift(start, end)
    _check_array(out, days, "lift()")
    _require(pa.types.is_float64(out.type), f"lift() must return float64, got {out.type}")
    vals = out.to_pylist()
    _require(
        all(v is not None and math.isfinite(v) and v >= 0 for v in vals),
        "lift() factors must be finite and non-negative (1.0 means no change)",
    )
    _require(_same(out, obj.lift(start, end)), "lift() is not deterministic")
    mid = start + timedelta(days=days // 2)
    sub = obj.lift(mid, end)
    _require(
        len(sub) == (end - mid).days + 1 and sub.to_pylist() == vals[days // 2 :],
        "lift() for a sub-range must equal the same days of the full range",
    )


def check_domain(obj: Any) -> None:
    """``Domain``: ``definition()`` returns a ``DomainDefinition`` with a schema and tables."""
    check_common(obj, "shape.domains")
    d = obj.definition()
    _require(isinstance(d, v1.DomainDefinition), "definition() must return a DomainDefinition")
    _require(isinstance(d.schema, Mapping) and len(d.schema) > 0, "`schema` must be non-empty")
    _require(
        all(isinstance(t, pa.Table) for t in d.reference_data.values()),
        "reference_data must map names to pyarrow Tables",
    )
    for preset, sizes in d.scale_presets.items():
        _require(
            all(isinstance(n, int) and n >= 0 for n in sizes.values()),
            f"scale preset {preset!r} must map tables to non-negative row counts",
        )
    _require(
        _same_json(d.schema, obj.definition().schema),
        "definition() must return the same schema every time",
    )


def _same_json(a: Any, b: Any) -> bool:
    try:
        return json.dumps(a, sort_keys=True, default=str) == json.dumps(
            b, sort_keys=True, default=str
        )
    except (TypeError, ValueError):
        return bool(a == b)


def check_chaos(obj: Any, batch: pa.RecordBatch, *, seed: int = 1) -> None:
    """``ChaosMutator``: a ``(RecordBatch, ChaosReport)`` that keeps the schema, reports an
    honest row count, and is deterministic for a seed."""
    check_common(obj, "shape.chaos")
    snapshot = pa.Table.from_batches([batch])
    out = obj.mutate(batch, seed)
    _require(isinstance(out, tuple) and len(out) == 2, "mutate() must return (batch, report)")
    mutated, report = out
    _require(isinstance(mutated, pa.RecordBatch), "mutate() must return a RecordBatch first")
    _require(isinstance(report, v1.ChaosReport), "mutate() must return a ChaosReport second")
    _require(mutated.schema.equals(batch.schema), "mutate() must keep the batch schema")
    _require(
        isinstance(report.rows_affected, int) and report.rows_affected >= 0,
        "ChaosReport.rows_affected must be a non-negative int",
    )
    _require(
        isinstance(report.mutator, str) and bool(report.mutator),
        "ChaosReport.mutator must be a non-empty string",
    )
    again, report2 = obj.mutate(batch, seed)
    _require(
        _same(mutated, again) and report == report2,
        "mutate() is not deterministic for a fixed seed",
    )
    _require(pa.Table.from_batches([batch]).equals(snapshot), "mutate() modified its input batch")


def check_emitter(obj: Any, uri: str, batches: Iterable[pa.RecordBatch]) -> None:
    """``Emitter``: sends every batch and returns the number of events sent."""
    check_common(obj, "shape.emitters")
    sample = list(batches)
    _require(len(sample) > 0, "sample `batches` must not be empty")
    sent = obj.emit(uri, iter(sample))
    _require(
        isinstance(sent, int) and not isinstance(sent, bool),
        "emit() must return the number of events sent (an int)",
    )
    _require(
        sent == sum(b.num_rows for b in sample),
        f"emit() returned {sent} for {sum(b.num_rows for b in sample)} events",
    )


def check_stream_source(obj: Any, uri: str) -> None:
    """``StreamSource``: yields ``(offset, batch)`` with JSON-serialisable offsets, and reading
    from the offset after batch *k* yields exactly the batches after *k* (checkpoint resume)."""
    check_common(obj, "shape.stream_sources")
    items = list(obj.read(uri))
    _require(len(items) > 0, "read() yielded nothing for the sample URI")
    for off, b in items:
        _require(isinstance(off, v1.StreamOffset), "read() must yield (StreamOffset, batch)")
        _require(isinstance(b, pa.RecordBatch), "read() must yield RecordBatches")
        try:
            json.dumps(dict(off.value))
        except (TypeError, ValueError):
            raise ConformanceError("StreamOffset.value must be JSON-serialisable") from None
    first_offset = items[0][0]
    resumed = list(obj.read(uri, start=first_offset))
    _require(
        len(resumed) == len(items) - 1
        and all(_same(a[1], b[1]) for a, b in zip(resumed, items[1:], strict=True)),
        "read(start=offset) must resume right after the batch that offset follows",
    )


def check_transform(obj: Any, tables: Mapping[str, pa.Table]) -> None:
    """``Transform``: tables in, a ``dict`` of tables out; deterministic; inputs untouched."""
    check_common(obj, "shape.transforms")
    snapshot = dict(tables)
    given = dict(tables)
    out = obj.apply(given)
    _require(
        given.keys() == snapshot.keys() and all(given[k] is snapshot[k] for k in snapshot),
        "apply() modified the tables mapping it was given",
    )
    _require(isinstance(out, dict), "apply() must return a dict of tables")
    _require(
        all(isinstance(k, str) and isinstance(v, pa.Table) for k, v in out.items()),
        "apply() must map table names to pyarrow Tables",
    )
    again = obj.apply(dict(tables))
    _require(
        out.keys() == again.keys() and all(_same(out[k], again[k]) for k in out),
        "apply() is not deterministic",
    )


_COMMAND_NAME = re.compile(r"^[a-z][a-z0-9-]*$")


def check_command(obj: Any, argv: Sequence[str] = (), *, expect_exit: int | None = 0) -> None:
    """``Command``: a ``shape <name>`` subcommand. ``configure`` must accept a fresh argparse
    parser, ``argv`` must parse, and ``run`` must return an ``int`` (``expect_exit``, if not
    ``None``, is the code it must return)."""
    check_common(obj, "shape.commands")
    _require(
        bool(_COMMAND_NAME.match(obj.name)),
        f"command name {obj.name!r} must be lowercase letters, digits and '-'",
    )
    _require(isinstance(obj.help, str) and bool(obj.help), "`help` must be a non-empty string")
    parser = argparse.ArgumentParser(prog=f"shape {obj.name}", exit_on_error=False)
    obj.configure(parser)
    try:
        args = parser.parse_args(list(argv))
    except (argparse.ArgumentError, SystemExit) as exc:
        raise ConformanceError(f"configure() does not accept argv {list(argv)!r}: {exc}") from None
    code = obj.run(args)
    _require(
        isinstance(code, int) and not isinstance(code, bool),
        f"run() must return the exit code (an int), got {type(code).__name__}",
    )
    if expect_exit is not None:
        _require(code == expect_exit, f"run() returned {code}, expected {expect_exit}")


def check_report_format(obj: Any, report: Mapping[str, Any]) -> None:
    """``ReportFormat``: a dotted ``extension`` and ``render()`` returning deterministic bytes."""
    check_common(obj, "shape.reports")
    _require(
        isinstance(obj.extension, str) and obj.extension.startswith(".") and len(obj.extension) > 1,
        "`extension` must be a string such as '.html'",
    )
    before = copy.deepcopy(dict(report))
    out = obj.render(report)
    _require(isinstance(out, (bytes, bytearray)), "render() must return bytes")
    _require(bytes(out) == bytes(obj.render(report)), "render() is not deterministic")
    _require(dict(report) == before, "render() modified its `report` argument")


CHECKS: dict[str, Callable[..., None]] = {
    "shape.sources": check_source,
    "shape.sinks": check_sink,
    "shape.detectors": check_detector,
    "shape.fitters": check_fitter,
    "shape.strategies": check_strategy,
    "shape.distributions": check_distribution,
    "shape.calendars": check_calendar,
    "shape.domains": check_domain,
    "shape.chaos": check_chaos,
    "shape.emitters": check_emitter,
    "shape.stream_sources": check_stream_source,
    "shape.transforms": check_transform,
    "shape.commands": check_command,
    "shape.reports": check_report_format,
}
"""Entry-point group -> the check for its Protocol."""


def check_plugin(group: str, obj: Any, **sample: Any) -> None:
    """Run the check for ``group`` on ``obj``; ``sample`` is passed to it (see each check).

    With no sample, only the rules every plugin shares are checked (:func:`check_common`)."""
    _require(group in CHECKS, f"unknown plugin group {group!r}; known: {sorted(CHECKS)}")
    if not sample and group not in ("shape.calendars", "shape.domains"):
        check_common(obj, group)
        return
    CHECKS[group](obj, **sample)


# -- whole distributions --------------------------------------------------------------------


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def check_installed(
    distribution: str, samples: Mapping[str, Mapping[str, Any]] | None = None
) -> list[str]:
    """Check every ``shape.*`` entry point an installed distribution declares.

    Each is loaded through the plugin host (so the API version, the Protocol and load-failure
    rules of the host apply) and then checked. ``samples`` maps ``"group:name"`` to the keyword
    sample for that plugin's check (see :func:`check_plugin`); a plugin with no sample gets the
    shared rules only. Returns one line per plugin; raises :class:`ConformanceError` on the
    first failure, or when the distribution declares no plugins.
    """
    samples = samples or {}
    try:
        dist = metadata.distribution(distribution)
    except metadata.PackageNotFoundError:
        raise ConformanceError(f"distribution {distribution!r} is not installed") from None
    declared = [ep for ep in dist.entry_points if ep.group in v1.GROUPS]
    _require(bool(declared), f"{distribution} declares no entry points in {sorted(v1.GROUPS)}")
    wanted = _norm(dist.metadata["Name"])
    host = PluginHost()
    lines: list[str] = []
    for ep in sorted(declared, key=lambda e: (e.group, e.name)):
        key = f"{ep.group}:{ep.name}"
        rec = host.record(ep.group, ep.name)
        _require(rec is not None, f"{key}: not discovered by the plugin host")
        assert rec is not None
        _require(
            _norm(rec.source) == wanted,
            f"{key}: the host registered {rec.source!r} under this name instead (duplicate)",
        )
        loaded = host.get(ep.group, ep.name)  # PluginLoadError carries the reason
        _require(
            getattr(loaded, "name", None) == ep.name,
            f"{key}: the object's `name` is {getattr(loaded, 'name', None)!r}, so the "
            f"entry-point name and `name` must match",
        )
        module = importlib.import_module(ep.value.partition(":")[0])
        check_module_api(module)
        sample = samples.get(key)
        check_plugin(ep.group, loaded, **dict(sample or {}))
        lines.append(f"{key}: ok" + ("" if sample else " (shared rules only: no sample given)"))
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m shape.plugins.kit DIST [--samples MODULE:ATTR]``.

    ``MODULE:ATTR`` names a dict ``{"group:name": {sample kwargs}}``. Exit 0 if every plugin
    of the distribution conforms, 1 if one does not, 2 for a usage error."""
    p = argparse.ArgumentParser(
        prog="python -m shape.plugins.kit",
        description="Check an installed Shape plugin distribution against plugin API v1.",
    )
    p.add_argument("distribution")
    p.add_argument("--samples", help="MODULE:ATTR of a dict mapping 'group:name' to a sample")
    ns = p.parse_args(argv)
    samples: Mapping[str, Mapping[str, Any]] | None = None
    if ns.samples:
        mod, _, attr = ns.samples.partition(":")
        if not attr:
            p.error("--samples must be MODULE:ATTR")
        try:
            samples = getattr(importlib.import_module(mod), attr)
        except (ModuleNotFoundError, ImportError) as exc:
            p.error(f"cannot import module '{mod}': {exc}")
        except AttributeError as exc:
            p.error(f"module '{mod}' has no attribute '{attr}': {exc}")
    try:
        lines = check_installed(ns.distribution, samples)
    except (ConformanceError, KeyError) as exc:
        print(f"FAIL {ns.distribution}: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # a plugin that fails to load is a failure, not a crash
        print(f"FAIL {ns.distribution}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    for line in lines:
        print(line)
    print(f"OK {ns.distribution}: {len(lines)} plugin(s) conform to plugin API {v1.SHAPE_API}")
    return 0


__all__ = [
    "CHECKS",
    "ConformanceError",
    "check_calendar",
    "check_chaos",
    "check_command",
    "check_common",
    "check_detector",
    "check_distribution",
    "check_installed",
    "check_domain",
    "check_emitter",
    "check_fitter",
    "check_module_api",
    "check_plugin",
    "check_report_format",
    "check_sink",
    "check_source",
    "check_stream_source",
    "check_strategy",
    "check_transform",
    "main",
]

if __name__ == "__main__":
    sys.exit(main())
