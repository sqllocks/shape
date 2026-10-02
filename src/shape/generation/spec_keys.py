"""The keys each strategy reads from a column's ``generator``: a key outside the set is ignored by
the strategy (a typo falls back to a default), so :meth:`GenSchema.validate` reports it.

The sets follow ``docs/GENERATION_STRATEGIES.md`` and the strategies in
``shape.builtins.strategies``; ``tests/generation/test_iss_gen_spec_keys.py`` checks that no
strategy reads a key missing here. A strategy that is not listed (a plugin's) is not checked.
"""

from __future__ import annotations

import difflib
from collections.abc import Mapping
from typing import Any

COMMON = frozenset({"strategy", "output_type"})

# What a column, not its generator, carries: `scale` rounds a number, `null_rate` makes nulls.
COLUMN_PROPERTIES = frozenset(
    {"name", "type", "scale", "precision", "max_length", "nullable", "null_rate", "primary_key"}
)

_TEMPORAL = frozenset(
    {
        "pattern",
        "start",
        "end",
        "date_range",
        "range",
        "range_ref",
        "profiles",
        "month_weights",
        "day_of_week_weights",
        "granularity",
        "unit",
    }
)

STRATEGY_KEYS: dict[str, frozenset[str]] = {
    "sequence": frozenset({"start", "step"}),
    "uuid": frozenset(),
    "constant": frozenset({"value"}),
    "choice": frozenset({"values", "weights"}),
    "uniform": frozenset({"low", "high"}),
    "normal": frozenset({"mean", "stddev"}),
    "weighted_enum": frozenset({"values"}),
    "distribution": frozenset({"distribution", "params", "min", "max"}),  # + the family's own
    "empirical": frozenset({"quantiles", "interpolation", "min", "max"}),
    "pattern": frozenset({"format"}),
    "native": frozenset({"provider", "args", "domains", "range"}),
    "faker": frozenset({"provider", "args", "domains", "range"}),
    "formula": frozenset({"expression"}),
    "derived": frozenset({"source", "rule", "operation", "via", "params", "days"}),
    "computed": frozenset({"rule", "child_table", "child_column"}),
    "correlated": frozenset({"source_column", "rule", "operation", "params"}),
    "lookup": frozenset({"source_table", "source_column", "via", "key"}),
    "conditional": frozenset({"condition", "true_generator", "false_generator"}),
    "reference_data": frozenset({"dataset", "field"}),
    "bootstrap": frozenset({"dataset", "field", "jitter"}),
    "record_sample": frozenset({"dataset", "field", "unique"}),
    "record_field": frozenset({"dataset", "field"}),
    "temporal": _TEMPORAL,
    "foreign_key": frozenset(
        {
            "ref",
            "distribution",
            "alpha",
            "max_per_parent",
            "params",
            "fan_out",
            "constrained_by",
            "sample_rate",
            "filter",
        }
    ),
    "composite_foreign_key": frozenset({"ref_table", "ref_columns", "distribution", "params"}),
    "composite_fk_field": frozenset({"source_column", "ref_column"}),
    "first_per_parent": frozenset({"parent_column", "default"}),
    "self_referencing": frozenset({"pk_column", "levels", "max_depth", "root_count"}),
    "self_ref_field": frozenset({"field"}),
    "lifecycle": frozenset({"phases", "values"}),
    "scd2": frozenset({"role", "business_key", "min_gap_days", "effective_date_column"}),
    "address": frozenset({"reference", "scope", "weights", "mode", "field"}),
}

# The keys of a nested ``params`` of a strategy that has one (``distribution`` takes its
# parameters either at the top or there).
PARAMS_KEYS: dict[str, frozenset[str]] = {
    "derived": frozenset({"distribution", "min", "max", "mean", "sigma", "std_dev"}),
    "correlated": frozenset({"factor_min", "factor_max", "offset_min", "offset_max", "min", "max"}),
    "foreign_key": frozenset({"alpha", "max_per_parent"}),
    "composite_foreign_key": frozenset({"alpha"}),
}

# The parameters of each distribution family as a spec spells them (``families.py``;
# ``tests/generation/test_iss_gen_spec_keys.py`` keeps this equal to the families). A family
# that is not listed (``histogram``, ``mixture``, ``truncated``, a plugin's) is not checked.
FAMILY_KEYS: dict[str, frozenset[str]] = {
    "uniform": frozenset({"low", "high", "min", "max"}),
    "normal": frozenset({"mu", "mean", "std_dev", "sigma", "std"}),
    "log_normal": frozenset({"mu", "mean", "sigma", "std"}),
    "pareto": frozenset({"alpha", "xm", "min"}),
    "zipf": frozenset({"a", "alpha", "max"}),
    "geometric": frozenset({"p"}),
    "poisson": frozenset({"lam", "lambda"}),
    "bernoulli": frozenset({"p", "probability"}),
    "exponential": frozenset({"lam", "lambda", "rate"}),
    "gamma": frozenset({"k", "theta"}),
    "beta": frozenset({"a", "b"}),
    "weibull": frozenset({"k", "lam"}),
    "triangular": frozenset({"low", "mode", "high"}),
    "negative_binomial": frozenset({"r", "p"}),
    "power_law_cutoff": frozenset({"alpha", "lam", "xmin"}),
}


def _family_keys(name: str) -> frozenset[str] | None:
    return FAMILY_KEYS.get(name)


def allowed_keys(strategy: str, generator: Mapping[str, Any]) -> frozenset[str] | None:
    """The keys ``strategy`` reads from ``generator``, or ``None`` when they are not known (a
    strategy that is not built in, or a ``distribution`` that a plugin supplies)."""
    base = STRATEGY_KEYS.get(strategy)
    if base is None:
        return None
    if strategy == "distribution":
        family = _family_keys(str(generator.get("distribution", "uniform")))
        if family is None:
            return None
        return COMMON | base | family
    return COMMON | base


def unknown_keys(strategy: str, generator: Mapping[str, Any]) -> list[tuple[str, str]]:
    """``(key, message)`` for every key of ``generator`` that ``strategy`` does not read. The
    message says what a column property is, or what the closest key is."""
    allowed = allowed_keys(strategy, generator)
    if allowed is None:
        return []
    found = [(k, _message(strategy, k, allowed, "")) for k in generator if k not in allowed]
    nested = generator.get("params")
    if isinstance(nested, Mapping):
        inner = _params_allowed(strategy, generator, allowed)
        if inner is not None:
            found += [
                (f"params.{k}", _message(strategy, k, inner, "params."))
                for k in nested
                if k not in inner
            ]
    return found


def _params_allowed(
    strategy: str, generator: Mapping[str, Any], allowed: frozenset[str]
) -> frozenset[str] | None:
    if strategy == "distribution":
        return allowed - {"params", "distribution", "strategy", "output_type"}
    return PARAMS_KEYS.get(strategy)


def _message(strategy: str, key: str, allowed: frozenset[str], prefix: str) -> str:
    where = f"'{prefix}{key}'"
    if key in COLUMN_PROPERTIES and not prefix:
        return (
            f"Strategy '{strategy}' ignores {where}: `{key}` is a column property, "
            "not a generator key"
        )
    close = difflib.get_close_matches(key, sorted(allowed), n=1, cutoff=0.6)
    hint = f" (did you mean '{prefix}{close[0]}'?)" if close else ""
    return f"Strategy '{strategy}' ignores unknown key {where}{hint}"
