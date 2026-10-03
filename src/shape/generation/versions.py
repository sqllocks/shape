"""Generator versions: which algorithm made the data, and pinning it.

See ``docs/GENERATION_STABILITY.md``.

Every strategy and every distribution declares an integer ``generator_version`` (a missing one
means 1). The version names the algorithm that maps parameters, seed and row index to values:
anything that changes the output for the same inputs raises it, and the previous version stays
implemented and selectable for the whole 1.x line. A spec can pin versions with its top-level
``generators`` map, ``{"<strategy or distribution name>": <int>}``; a name that is not pinned runs
at the latest version.

An implementation that has more than one version provides the selector:

* a strategy: ``generate_versioned(spec, ctx, version)``;
* a distribution plugin: ``sample_versioned(params, ctx, version)``;
* a built-in distribution family: ``sample_versioned(stream, row_start, n, params, version)``.

The engine calls the selector with the version in effect (pinned or latest). An implementation
without one has the one version it declares, and a pin to another is an error. A strategy and a
distribution may share a name (``uniform``, ``normal``); a pin of that name applies to both and
must be valid for both.

Stable interface: ``generator_version``, :func:`pins_of`, :func:`usage_of`, :func:`check_pins` and
:class:`GeneratorPinError`.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from shape.errors import ShapeError

#: The key of a run manifest's ``reproducibility`` map that holds the versions used.
MANIFEST_KEY = "generators"

Lookup = Callable[[str], Any]


class GeneratorPinError(ShapeError, ValueError):
    """A spec pins a generator at a version this Shape does not have (exit code 2)."""

    def __init__(self, source: str, name: str, version: int, low: int, high: int) -> None:
        have = f"versions {low} to {high}" if low != high else f"version {low}"
        hint = "upgrade Shape" if version > high else "re-pin the spec"
        super().__init__(
            f"{source} pins {name} at generator version {version}; this Shape has {have} ({hint})"
        )
        self.name = name
        self.version = version
        self.low = low
        self.high = high


class GeneratorPinWarning(UserWarning):
    """A spec pins a name it does not use."""


def generator_version(obj: Any) -> int:
    """The version ``obj`` declares: its ``generator_version`` attribute, 1 when it has none."""
    declared = getattr(obj, "generator_version", 1)
    if isinstance(declared, bool) or not isinstance(declared, int) or declared < 1:
        raise ValueError(
            f"{type(obj).__name__}.generator_version must be an integer of at least 1, "
            f"got {declared!r}"
        )
    return declared


def selectable(obj: Any) -> bool:
    """Whether ``obj`` can run an older version than the one it declares."""
    return any(
        callable(getattr(obj, attr, None)) for attr in ("generate_versioned", "sample_versioned")
    )


def supported(obj: Any) -> tuple[int, int]:
    """The lowest and highest version ``obj`` can run: ``1`` to its declared version when it can
    select a version, else only the one it declares. An object that wraps another (a distribution
    entry for a family) states the range itself in ``generator_version_range``."""
    stated = getattr(obj, "generator_version_range", None)
    if stated is not None:
        low, high = stated
        return int(low), int(high)
    latest = generator_version(obj)
    return (1, latest) if selectable(obj) else (latest, latest)


def pins_of(doc: Mapping[str, Any] | None) -> dict[str, int]:
    """The ``generators`` map of a spec document (empty when it has none)."""
    raw = (doc or {}).get("generators") or {}
    return {str(k): int(v) for k, v in raw.items()}


def _strategy_lookup() -> Lookup:
    from shape.plugins.host import default_host

    host = default_host()
    return lambda name: host.try_get("shape.strategies", name)


def _distribution_lookup() -> Lookup:
    from shape.plugins.host import default_host

    host = default_host()
    return lambda name: host.try_get("shape.distributions", name)


def _distribution_name(generator: Mapping[str, Any]) -> str | None:
    """The distribution a ``distribution`` column samples (``uniform`` when it names none). Other
    strategies that take a ``distribution`` key have their own algorithm, which their version
    covers."""
    if generator.get("strategy") != "distribution":
        return None
    name = generator.get("distribution", "uniform")
    return name if isinstance(name, str) and name else "uniform"


def usage_of(
    tables: Mapping[str, Any],
    strategy_lookup: Lookup | None = None,
    distribution_lookup: Lookup | None = None,
) -> dict[str, list[Any]]:
    """Every strategy and distribution the spec uses that has an implementation:
    ``{name: [implementation, ...]}`` in name order. ``tables`` is ``GenSchema.tables``.
    A name that resolves to nothing (``computed``, a plugin that is not installed) is left out."""
    find_strategy = strategy_lookup or _strategy_lookup()
    find_distribution = distribution_lookup or _distribution_lookup()
    found: dict[str, list[Any]] = {}

    def add(name: str, impl: Any) -> None:
        if impl is not None and not any(impl is seen for seen in found.setdefault(name, [])):
            found[name].append(impl)

    for table in tables.values():
        for column in table.columns.values():
            generator = column.generator
            strategy = str(generator.get("strategy", ""))
            if strategy:
                add(strategy, find_strategy(strategy))
            dist = _distribution_name(generator)
            if dist is not None:
                resolve = getattr(find_strategy(strategy), "resolve_distribution", None)
                add(dist, resolve(dist) if callable(resolve) else find_distribution(dist))
    return {name: impls for name, impls in sorted(found.items()) if impls}


def version_range(impls: Iterable[Any]) -> tuple[int, int]:
    """The versions valid for every implementation behind one name."""
    spans = [supported(i) for i in impls]
    return max(s[0] for s in spans), min(s[1] for s in spans)


def current_versions(usage: Mapping[str, list[Any]]) -> dict[str, int]:
    """The latest version of every name in ``usage``."""
    return {name: version_range(impls)[1] for name, impls in usage.items()}


def check_pins(
    pins: Mapping[str, int],
    usage: Mapping[str, list[Any]],
    source: str = "the spec",
) -> list[str]:
    """Raise :class:`GeneratorPinError` for the first pin to a version this Shape does not have;
    return the names pinned but not used (the caller warns)."""
    for name in sorted(pins):
        if name not in usage:
            continue
        low, high = version_range(usage[name])
        if not low <= pins[name] <= high:
            raise GeneratorPinError(source, name, pins[name], low, high)
    return sorted(n for n in pins if n not in usage)


def warn_unused(unused: Iterable[str], source: str = "the spec") -> None:
    for name in unused:
        warnings.warn(
            f"{source} pins {name}, which it does not use",
            GeneratorPinWarning,
            stacklevel=3,
        )


def effective(pins: Mapping[str, int], usage: Mapping[str, list[Any]]) -> dict[str, int]:
    """The version of every name in ``usage`` in a run with ``pins``: the pin, else the latest."""
    latest = current_versions(usage)
    return {name: pins.get(name, latest[name]) for name in usage}
