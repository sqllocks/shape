"""The comparisons of the bridge parity harness: a baseline result and a Shape result in, the list
of differences out (empty: they agree). Pure functions on plain JSON, standard library only, so a
test can mutate a result and require the comparison to flag it (``--negative-control``).

Metadata is compared exactly. A difference is never normalised away silently: the few places that
are compared by meaning (an order of tables that may be any dependency order, the phases of a
lifecycle) say so where they are."""

from __future__ import annotations

from typing import Any

Problems = list[str]


def _diff_value(label: str, base: Any, shape: Any) -> Problems:
    return [] if base == shape else [f"{label}: baseline {base!r}, shape {shape!r}"]


def describe(base: dict[str, Any], shape: dict[str, Any]) -> Problems:
    out: Problems = []
    for key in ("domain", "mode", "table_count", "generation_order"):
        out += _diff_value(f"describe.{key}", base[key], shape[key])
    for name, bt in base["tables"].items():
        st = shape["tables"].get(name)
        if st is None:
            out.append(f"describe: table {name!r} missing from shape")
            continue
        for key in ("description", "primary_key", "column_count"):
            out += _diff_value(f"describe.{name}.{key}", bt[key], st[key])
        out += _diff_value(
            f"describe.{name}.dependencies", sorted(bt["dependencies"]), sorted(st["dependencies"])
        )
        bcols = [(c["name"], c["type"], c["nullable"]) for c in bt["columns"]]
        scols = [(c["name"], c["type"], c["nullable"]) for c in st["columns"]]
        out += _diff_value(f"describe.{name}.columns", bcols, scols)
    out += [
        f"describe: extra table {n!r} in shape" for n in shape["tables"] if n not in base["tables"]
    ]

    def rel(r: dict[str, Any]) -> tuple[Any, ...]:
        return (
            r["name"],
            r["parent"],
            r["child"],
            tuple(r["parent_columns"]),
            tuple(r["child_columns"]),
        )

    out += _diff_value(
        "describe.relationships",
        sorted(map(rel, base["relationships"])),
        sorted(map(rel, shape["relationships"])),
    )
    rules = lambda d: sorted((r["name"], r["rule"], r["type"]) for r in d["business_rules"])  # noqa: E731
    out += _diff_value("describe.business_rules", rules(base), rules(shape))
    out += _diff_value("describe.scales", base["scales"], shape["scales"])
    return out


def dry_run(base: dict[str, Any], shape: dict[str, Any]) -> Problems:
    out: Problems = []
    for key in ("domain", "scale", "generation_order", "planned_rows", "total_rows"):
        out += _diff_value(f"dry_run.{key}", base[key], shape[key])
    return out


def _phases(value: Any) -> Any:
    """A lifecycle's phases as ``{name: weight}``: the baseline lists them, Shape keeps a mapping,
    and their order carries no meaning."""
    if isinstance(value, dict) and isinstance(value.get("phases"), list):
        return {"phases": {p["name"]: p["weight"] for p in value["phases"]}}
    return value


def profile_info(
    base: dict[str, Any],
    shape: dict[str, Any],
    aliases: dict[str, str] | None = None,
    skip: tuple[str, ...] = (),
) -> Problems:
    """Every baseline key is in Shape's with the same value; Shape may report more (a superset
    breaks no client). ``aliases`` maps a baseline key to the key Shape reports it under (a baseline
    key that names no column) and ``skip`` lists keys compared elsewhere (both are allow-list
    entries, shown in ``verify.py``)."""
    aliases = aliases or {}
    out: Problems = []
    out += _diff_value("profile_info.domain", base["domain"], shape["domain"])
    out += _diff_value("profile_info.profile", base["profile"], shape["profile"])
    out += _diff_value(
        "profile_info.available_profiles", base["available_profiles"], shape["available_profiles"]
    )
    out += _diff_value("profile_info.ratios", base["ratios"], shape["ratios"])
    out += _diff_value("profile_info.ratio_keys", base["ratio_keys"], shape["ratio_keys"])
    mapped = {aliases.get(k, k): v for k, v in base["distributions"].items()}
    for key, value in mapped.items():
        if key not in shape["distributions"]:
            out.append(f"profile_info: distribution {key!r} missing from shape")
        elif key not in skip:
            out += _diff_value(
                f"profile_info.distributions[{key}]",
                _phases(value),
                _phases(shape["distributions"][key]),
            )
    out += [
        f"profile_info: key {k!r} is not in shape's distribution_keys"
        for k in mapped
        if k not in shape["distribution_keys"]
    ]
    out += _diff_value(
        "profile_info.distribution_keys is the sorted keys",
        shape["distribution_keys"],
        sorted(shape["distributions"]),
    )
    return out


def generate_summary(base: dict[str, Any], shape: dict[str, Any]) -> Problems:
    """Row counts and column counts per table, the totals and integrity: exact (the seeds differ,
    the shape of the output does not)."""
    out: Problems = []
    out += _diff_value("generate.domain", base["domain"], shape["domain"])
    out += _diff_value("generate.scale", base["scale"], shape["scale"])
    out += _diff_value("generate.total_rows", base["total_rows"], shape["total_rows"])
    out += _diff_value("generate.tables", base["tables"], shape["tables"])
    out += _diff_value("generate.integrity_pass", base["integrity_pass"], shape["integrity_pass"])
    out += _diff_value(
        "generate.integrity_errors", base["integrity_errors"], shape["integrity_errors"]
    )
    return out


def generate_files(
    base: dict[str, Any], shape: dict[str, Any], base_dir: str, shape_dir: str
) -> Problems:
    names = lambda d, root: sorted(f.removeprefix(root).lstrip("/") for f in d["files"])  # noqa: E731
    out = _diff_value("generate.files", names(base, base_dir), names(shape, shape_dir))
    out += _diff_value("generate.output_format", base["output_format"], shape["output_format"])
    return out


def _kinds(table: dict[str, Any], column: str) -> list[str]:
    return sorted({type(r[column]).__name__ for r in table["data"] if r[column] is not None})


def preview(base: dict[str, Any], shape: dict[str, Any]) -> Problems:
    """Per table: total rows, preview rows and the columns exact; every row has the columns; the
    values of each column have the same JSON types in both (nulls aside)."""
    out: Problems = []
    out += _diff_value("preview.domain", base["domain"], shape["domain"])
    out += _diff_value("preview.tables", sorted(base["tables"]), sorted(shape["tables"]))
    for name, bt in base["tables"].items():
        st = shape["tables"].get(name)
        if st is None:
            continue
        for key in ("total_rows", "preview_rows", "columns"):
            out += _diff_value(f"preview.{name}.{key}", bt[key], st[key])
        for label, table in (("baseline", bt), ("shape", st)):
            for row in table["data"]:
                if sorted(row) != sorted(table["columns"]):
                    out.append(f"preview.{name}: a {label} row's keys are not the columns")
                    break
        for column in bt["columns"]:
            out += _diff_value(
                f"preview.{name}.{column} value types", _kinds(bt, column), _kinds(st, column)
            )
    return out


def validate(base: dict[str, Any], shape: dict[str, Any]) -> Problems:
    out: Problems = []
    for key in ("valid", "table_count", "relationship_count"):
        out += _diff_value(f"validate.{key}", base[key], shape[key])
    for side, doc in (("baseline", base), ("shape", shape)):
        for issue in doc["errors"] + doc["warnings"]:
            if set(issue) != {"location", "message"}:
                out.append(f"validate: a {side} issue is not {{location, message}}")
    out += _diff_value("validate.has_errors", bool(base["errors"]), bool(shape["errors"]))
    return out


def scale_generate(base: dict[str, Any], shape: dict[str, Any], expected_rows: int) -> Problems:
    out: Problems = []
    for key in ("domain", "scale", "scale_mode", "sinks_written"):
        out += _diff_value(f"scale_generate.{key}", base[key], shape[key])
    out += _diff_value("scale_generate.rows_generated", expected_rows, shape["rows_generated"])
    for key in ("elapsed_seconds", "throughput_rows_per_sec"):
        if key not in shape:
            out.append(f"scale_generate: shape lacks {key!r}")
    return out


def stream_status(base: dict[str, Any], shape: dict[str, Any]) -> Problems:
    """The baseline's keys all exist in Shape's, and the counters that do not depend on the
    definition of ``rows_written`` agree."""
    out = [f"stream_status: shape lacks {k!r}" for k in base if k not in shape]
    out += _diff_value(
        "stream_status.chunks_written", base["chunks_written"], shape.get("chunks_written")
    )
    out += _diff_value("stream_status.running", base["running"], shape.get("running"))
    out += _diff_value("stream_status.error", base["error"], shape.get("error"))
    return out
