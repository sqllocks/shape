"""The ``Profile`` object returned by ``shape.profile`` and its ``.shape`` artifact form."""

from __future__ import annotations

import copy
import datetime as _dt
import hashlib
import math
import warnings
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np

from shape import compat
from shape.artifact import codec
from shape.artifact.io import ArtifactError, read_artifact, write_artifact
from shape.io.excel import is_workbook_spec
from shape.security.hardening import validate_structure

from .. import sampling as _sampling
from .column import MAX_VALUE_CHARS
from .model import ColumnProfile, DatasetProfile, TableProfile
from .readers import CsvFormat, single_threaded_pools
from .sources import SourceError, check_delta_options, delta_dir, load_columns, read_delta
from .table import _profile_cols_table, profile_dataset_columns

ARTIFACT_FORMAT = "shape"
ARTIFACT_FORMAT_VERSION = compat.KINDS["profile-artifact"].current
ARTIFACT_KIND = "profile"
PROFILE_COMPONENT = "profile.json"

_COLUMN_FIELDS = (
    "name",
    "dtype",
    "null_count",
    "null_rate",
    "cardinality",
    "cardinality_ratio",
    "is_unique",
    "is_enum",
    "mean",
    "std",
    "distribution",
    "distribution_params",
    "pattern",
    "is_primary_key",
    "is_foreign_key",
    "fk_ref_table",
    "quantiles",
    "hour_histogram",
    "dow_histogram",
    "temporal_histogram",
    "string_length",
    "outlier_rate",
    "fit_score",
    "nan_count",
    "inf_count",
    "pattern_rates",
    "pattern_contains_rates",
    "precision",
    "scale",
)


_PLAIN = frozenset({str, int, bool, type(None)})  # leaves _clean returns unchanged
_STR_ONLY = frozenset({str})


_NUMBERS = frozenset({int, float, bool})


def _no_nan(values: Any) -> bool:
    """True when every value is a leaf ``_clean`` returns unchanged: plain str/int/bool/None, or
    numbers without NaN (a sum is NaN when any term is; an inf - inf false alarm only costs the
    slow path)."""
    types = set(map(type, values))
    if _PLAIN.issuperset(types):
        return True
    if _NUMBERS.issuperset(types):
        total = sum(values)
        return bool(total == total)
    return False


def _clean(v: Any) -> Any:
    """NaN becomes the string ``"NaN"``; numpy scalars become Python scalars."""
    t = type(v)
    if t is str or t is int or t is bool or v is None:  # the common leaves, with no further checks
        return v
    if t is float:
        return "NaN" if v != v else v
    if isinstance(v, float) and math.isnan(v):
        return "NaN"
    if isinstance(v, dict):
        # value counts: str keys over plain values need no per-entry work (types checked in C)
        if _STR_ONLY.issuperset(map(type, v)) and _no_nan(v.values()):
            return dict(v)
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        if _no_nan(v):
            return list(v)
        return [_clean(x) for x in v]
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        return _clean(v.item())
    return v


def _tag_scalar(v: Any) -> list[Any] | None:
    """Tag a min/max value with its Python type so type drift stays visible."""
    if v is None:
        return None
    type_name = type(v).__name__
    if isinstance(v, bool):
        return ["bool", bool(v)]
    if isinstance(v, int):
        return ["int", int(v)]
    if isinstance(v, float):
        return ["float", None if math.isnan(v) else float(v)]
    if isinstance(v, str):
        return ["str", _cut(v)]
    if type_name == "Timestamp":
        return ["timestamp", str(v)]
    if isinstance(v, _dt.datetime):
        return ["datetime", str(v)]
    if isinstance(v, _dt.date):
        return ["date", str(v)]
    return [type_name, _cut(str(v))]  # bytes and the rest are cut like text (#317)


def _cut(text: str) -> str:
    return text if len(text) <= MAX_VALUE_CHARS else text[:MAX_VALUE_CHARS] + "\u2026"


def _column_dict(cp: ColumnProfile) -> dict[str, Any]:
    d = {f: _clean(getattr(cp, f)) for f in _COLUMN_FIELDS}
    for f in ("nan_count", "inf_count"):  # absent when zero, like the other optional fields
        if not d[f]:
            del d[f]
    if cp.placeholders:  # absent when none, like the other optional fields
        d["placeholders"] = cp.placeholders
    if cp.univariate:  # the univariate depth fields, each present only where it applies
        d.update(cp.univariate)
    d["min_value"] = _tag_scalar(cp.min_value)
    d["max_value"] = _tag_scalar(cp.max_value)
    d["enum_values"] = _clean(cp.enum_values)
    d["value_counts_ext"] = _clean(cp.value_counts_ext)
    # key order is the frequency order of the top values; keep it explicitly
    d["value_counts_ext_order"] = list(cp.value_counts_ext) if cp.value_counts_ext else None
    if cp.adequacy is not None:  # absent in a profile written before W2-07
        d["adequacy"] = cp.adequacy
    if cp.type_inference is not None:
        d["type_inference"] = cp.type_inference
    if cp.structure is not None:
        d["row_count"] = cp.null_count + int((cp.serialized_size or {}).get("count", 0))
        d["structure"] = cp.structure
        d["serialized_size"] = cp.serialized_size
    return d


def table_to_dict(tp: TableProfile) -> dict[str, Any]:
    """A table profile as a JSON-ready dict."""
    out = {
        "name": tp.name,
        "row_count": tp.row_count,
        "primary_key": list(tp.primary_key),
        "detected_fks": dict(tp.detected_fks),
        "correlation_matrix": _clean(tp.correlation_matrix),
        "columns": {c: _column_dict(cp) for c, cp in tp.columns.items()},
    }
    if tp.correlation_truncated:
        out["correlation_truncated"] = True
    if tp.joint:
        out["joint"] = tp.joint
    if tp.sampling is not None:  # absent in a profile written before W2-07
        out["sampling"] = tp.sampling
    return out


def dataset_to_dict(dp: DatasetProfile) -> dict[str, Any]:
    """A multi-table profile as a JSON-ready dict."""
    return {
        "tables": {n: table_to_dict(t) for n, t in dp.tables.items()},
        "relationships": _clean(dp.relationships),
    }


def _plain(tagged: Any) -> Any:
    """``["int", 5]`` -> ``5`` for summaries."""
    if isinstance(tagged, list) and len(tagged) == 2:
        return tagged[1]
    return None


def _column_summary(col: dict[str, Any]) -> dict[str, Any]:
    out = {
        "dtype": col["dtype"],
        "null_rate": col["null_rate"],
        "cardinality": col["cardinality"],
        "is_unique": col["is_unique"],
        "is_primary_key": col["is_primary_key"],
        "is_foreign_key": col["is_foreign_key"],
        "fk_ref_table": col["fk_ref_table"],
        "distribution": col["distribution"],
        "pattern": col["pattern"],
        "min": _plain(col["min_value"]),
        "max": _plain(col["max_value"]),
        "mean": col["mean"],
        "std": col["std"],
    }
    if col.get("dtype") == "nested":
        out.update(structure=col.get("structure"), serialized_size=col.get("serialized_size"))
    return out


def _table_summary(table: dict[str, Any]) -> dict[str, Any]:
    out = {
        "name": table["name"],
        "row_count": table["row_count"],
        "primary_key": list(table["primary_key"]),
        "columns": {c: _column_summary(col) for c, col in table["columns"].items()},
    }
    if table.get("findings"):
        out["findings"] = copy.deepcopy(table["findings"])
    if table.get("sampling") is not None:
        out["sampling"] = copy.deepcopy(table["sampling"])
    return out


class Profile:
    """A data profile. ``to_dict()`` is the full profile as JSON-ready dicts."""

    def __init__(
        self,
        data: dict[str, Any],
        *,
        name: str | None = None,
        provenance: dict[str, Any] | None = None,
        sketches: dict[str, Any] | None = None,
        capture: dict[str, Any] | None = None,
        redaction_manifest: dict[str, Any] | None = None,
    ) -> None:
        if "tables" not in data and "columns" not in data:
            raise ValueError("not a profile: expected a table or dataset profile dictionary")
        self._data = data
        self._sketches = sketches
        self._provenance = None if provenance is None else dict(provenance)
        self._capture = None if capture is None else dict(capture)
        self._redaction = {} if redaction_manifest is None else copy.deepcopy(redaction_manifest)
        self.name = name or (data.get("name") if "columns" in data else None) or "dataset"

    @property
    def provenance(self) -> dict[str, Any] | None:
        """Where the data came from, for a source that has a state to name: a Delta table's
        ``format``, ``version``, commit ``timestamp`` and the ``as_of`` asked for. ``None``
        otherwise. It is kept in the ``.shape`` file's manifest, not in the profile body, so it
        never changes the content id or a ``shape.diff``."""
        return None if self._provenance is None else dict(self._provenance)

    @property
    def sketches(self) -> dict[str, Any] | None:
        """The optional sketch state (``shape.profile(..., sketches=True)``) that lets this
        profile be merged for its approximate statistics; ``None`` without it. It is kept in the
        ``.shape`` file beside the profile body, so it never changes the content id."""
        return None if self._sketches is None else copy.deepcopy(self._sketches)

    @property
    def content_id(self) -> str:
        """The sha256 of the profile body: what ``save`` returns and the file's
        ``shape_content_id``."""
        return hashlib.sha256(_encode(self._data)).hexdigest()

    @property
    def merged_from(self) -> list[dict[str, Any]]:
        """For a profile made by ``merge_profiles``: its inputs, each with ``name``,
        ``shape_content_id`` and ``row_count``, in merge order. Empty for any other profile."""
        merge = self._data.get("merge")
        inputs = merge.get("inputs") if isinstance(merge, dict) else None
        return copy.deepcopy(inputs) if isinstance(inputs, list) else []

    @property
    def capture_declared(self) -> bool:
        """True when the profile says how it was captured: it came from a ``.shape`` artifact that
        declares it, or from :func:`shape.privacy.redact.redact_profile`. A profile from
        ``shape.profile`` or from an artifact written before capture modes existed does not."""
        return self._capture is not None

    @property
    def capture(self) -> dict[str, Any]:
        """How the profile was captured: ``{"mode": "safe" | "full", "k": N}`` (``k`` is ``None``
        for a full capture). A profile that does not say reads as ``{"mode": "full", "k": None}``:
        it holds what the data held."""
        return dict(self._capture) if self._capture is not None else {"mode": "full", "k": None}

    @property
    def redaction_manifest(self) -> dict[str, Any]:
        """What a safe capture suppressed, per table and column (empty for a full capture)."""
        return copy.deepcopy(self._redaction)

    def with_capture(self, capture: dict[str, Any], redaction_manifest: dict[str, Any]) -> Profile:
        """The same profile data stamped with how it was captured (the data is shared, not
        copied: do not modify what :meth:`to_dict` has not copied)."""
        return Profile(
            self._data,
            name=self.name,
            provenance=self._provenance,
            sketches=self._sketches,
            capture=capture,
            redaction_manifest=redaction_manifest,
        )

    @property
    def is_dataset(self) -> bool:
        """True for a multi-table profile."""
        return "tables" in self._data

    @property
    def tables(self) -> dict[str, dict[str, Any]]:
        """Table profile dictionaries by name (one entry for a single-table profile)."""
        if self.is_dataset:
            return copy.deepcopy(self._data["tables"])
        return {self._data["name"]: copy.deepcopy(self._data)}

    def to_dict(self) -> dict[str, Any]:
        """The full profile as JSON-ready dicts."""
        return copy.deepcopy(self._data)

    def summary(self) -> dict[str, Any]:
        """A small JSON-safe dict: name, row_count and per-column statistics."""
        if not self.is_dataset:
            out = _table_summary(self._data)
            out["name"] = self.name if self.name else out["name"]
            if self._capture is not None and self._capture["mode"] == "safe":
                out["capture"] = dict(self._capture)  # a full capture's summary is as it was
            return out
        out = {
            "name": self.name,
            "row_count": sum(t["row_count"] for t in self._data["tables"].values()),
            "tables": {n: _table_summary(t) for n, t in self._data["tables"].items()},
            "relationships": copy.deepcopy(self._data["relationships"]),
        }
        if self._data.get("findings"):
            out["findings"] = copy.deepcopy(self._data["findings"])
        if self._capture is not None and self._capture["mode"] == "safe":
            out["capture"] = dict(self._capture)
        return out

    def to_html(self) -> str:
        """A self-contained HTML report (no external assets)."""
        from shape.report import render_html

        return render_html(self)

    def _repr_html_(self) -> str:
        """Notebook display: a small table of safe statistics (never extremes or categories)."""
        from shape.report.display import profile_html

        return profile_html(self)

    def _repr_markdown_(self) -> str:
        from shape.report.display import profile_markdown

        return profile_markdown(self)

    def sampling(self) -> dict[str, dict[str, Any] | None]:
        """The sampling record of every table (``None`` for a profile written before the record
        existed): how many rows were profiled, by which method, and whether that is adequate."""
        return {n: t.get("sampling") for n, t in self.tables.items()}

    def describe_sampling(self) -> str:
        """One line per table saying how much of the data this profile saw."""
        return "\n".join(f"{n}: {_sampling.describe(r)}" for n, r in self.sampling().items())

    def __repr__(self) -> str:
        sampled = [n for n, r in self.sampling().items() if r and r.get("method") != "none"]
        suffix = f", sampled={sampled}" if sampled else ""
        if self.is_dataset:
            return f"Profile(dataset, tables={list(self._data['tables'])}{suffix})"
        return (
            f"Profile({self._data['name']!r}, rows={self._data['row_count']}, "
            f"columns={len(self._data['columns'])}{suffix})"
        )

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Profile) and _encode(self._data) == _encode(other._data)

    __hash__ = None  # type: ignore[assignment]


def profile(
    source: Any,
    *,
    name: str | None = None,
    version: int | None = None,
    as_of: _dt.datetime | str | None = None,
    delimiter: str | None = None,
    encoding: str | None = None,
    quotechar: str | None = None,
    header: bool = True,
    string_columns: Iterable[str] = (),
    types: Mapping[str, str] | None = None,
    infer_types: str = "auto",
    reference_pairs: Any = None,
    joint: bool | None = None,
    sheet: str | None = None,
    include_hidden: bool = False,
    sketches: bool = False,
    univariate: bool = False,
    multivariate: bool = False,
    sample: int | float | str | None = None,
    sample_method: str = "random",
    sample_seed: int | None = None,
    decisions: Any = None,
    validators: Any = None,
    time_column: str | None = None,
    columns: Iterable[str] | None = None,
    exclude: Iterable[str] = (),
) -> Profile:
    """Profile a path, glob, directory, Delta table, Arrow table or DataFrame.

    Pass a ``dict`` of such sources to profile several tables and detect foreign keys.

    ``columns`` selects names in source order and ``exclude`` removes names afterwards.
    Unknown names are refused. Nested Arrow columns are opaque: counts, canonical JSON
    distinct counts, serialized byte sizes and structure, without example values.

    CSV options: ``delimiter`` (default: sniffed among comma, semicolon, tab and pipe),
    ``encoding`` (default UTF-8), ``quotechar`` (default ``"``) and ``header=False`` for a file
    without a header row (columns are then ``f0``, ``f1``, ...).

    Identifiers are not numbers: a CSV column of digits with leading zeros (``02134``), or of one
    fixed width of five or more digits whose name says it is an identifier (``zip``, ``npi``,
    ``member_id``), is read as text, so ``00000`` stays ``00000``. ``string_columns`` keeps more
    columns as text, ``types`` (``{"amount": "float"}``; string, integer, float, boolean, date,
    datetime) sets column types, and ``infer_types="off"`` reads every column as text. An integer
    column that only looks like an identifier is reported as a warning.

    For a Delta table directory, ``version=N`` profiles that version and ``as_of`` (a
    ``datetime``, naive meaning UTC, or an ISO-8601 string) the newest version committed at or
    before that time, instead of the latest; give one at most. ``Profile.provenance`` records
    which version was read.

    CSV options: ``delimiter`` (default: sniffed among comma, semicolon, tab and pipe),
    ``encoding`` (default UTF-8), ``quotechar`` (default ``"``) and ``header=False`` for a file
    without a header row (columns are then ``f0``, ``f1``, ...).

    ``reference_pairs`` checks that groups of columns hold real combinations: a list of
    ``{"columns": ["city", "zip"], "reference": <dataset name, file or table>}`` (for several
    tables, a dict of table name to such a list). The share of rows whose tuple is in the
    reference is stored in the table's ``joint`` entry, where the ``reference_pair`` contract
    rule and ``shape.diff`` read it.

    ``validators`` checks that a column's values are valid codes: ``{"zip": "us_zip", "country":
    ["iso3166_alpha2", "iso3166_alpha3"]}`` (for several tables, a dict of table name to such a
    dict). The kinds are ``iban``, ``iso3166_alpha2``, ``iso3166_alpha3``, ``iso4217``,
    ``iso639_1`` and ``us_zip`` (``docs/REFERENCE_PACKS.md``). The column then holds
    ``validators: {kind: {checked, valid, valid_rate}}``: counts and a rate, never a value, which
    the ``valid_as`` contract rule reads.

    ``joint`` chooses the joint analysis (dependencies, keys, associations): on by default for
    one table, off by default for a dataset (a dict of tables, a workbook), ``joint=True`` turns
    it on and ``joint=False`` off; without it ``SHAPE_PROFILE_JOINT`` decides (``0`` off, ``1`` on).

    ``decisions`` (a ``DecisionFile`` or its path) gives the CSV columns the types of its accepted
    ``type`` decisions, as ``types`` does; ``types`` wins for a column both name.

    ``sample`` profiles a sample of each table instead of all of it: an ``int`` is a number of rows,
    a ``float`` above 0 and up to 1 a fraction of them (``"10%"`` also works). ``sample_method`` is
    ``random`` (uniform, without replacement), ``systematic`` (evenly spread rows from a seeded
    start) or ``head`` (the first rows); ``sample_seed`` defaults to 42. The same rows are chosen
    on every run and in both kernel modes. Nothing is sampled without ``sample``, and every table's
    profile records what was done in ``sampling`` (``docs/PROFILING_NOTES.md``).

    ``time_column`` names the date or timestamp column that each numeric column's ``seasonality``
    is measured against (default: the table's only date or timestamp column; a dict of tables
    uses it for the tables that have it). It is an error when no table has the column, or when it
    is not a date or timestamp column. Workbook sheets are not analysed for seasonality.

    An ``.xlsx`` workbook is a dataset with one table per visible sheet (``"book.xlsx#Sheet"``
    or ``sheet=`` profiles that sheet alone, hidden or not; ``include_hidden=True`` reads the
    hidden sheets too), and the profile carries ``findings`` about its cells.

    ``sketches=True`` also keeps the mergeable sketch state (one more pass over the data,
    bounded memory), so the profile can be combined with others by
    ``shape.profile.merge_profiles`` for its approximate statistics. The profile itself, and
    its content id, are the same with or without it. It reads every row, so it cannot be
    combined with ``sample``.

    ``univariate=True`` adds the univariate depth fields to every numeric column
    (``distribution_candidates``, ``distribution_by_bic``, ``zero_share``, ``zero_inflation``,
    ``heaping``, ``benford``, ``tail_index``; ``docs/PROFILING_NOTES.md``), which ``shape.diff``
    compares when both profiles carry them. They are off by default: they add Python work for
    every numeric column.

    ``multivariate=True`` adds the multivariate entries to the joint analysis of each table
    (``multivariate_outliers``, ``pca``, ``cohorts`` and the mixed-type ``copula``;
    ``docs/JOINT.md``), which ``shape.diff`` compares and ``mixed_copula`` generation reads. They
    are off by default too: they add a fixed cost per table.
    """
    fmt = CsvFormat(
        delimiter,
        encoding,
        quotechar,
        header,
        tuple(string_columns),
        tuple((types or {}).items()),
        infer_types,
    )
    spec = _sampling.make_spec(sample, sample_method, sample_seed)
    selected = None if columns is None else tuple(columns)
    excluded = tuple(exclude)
    if sketches and spec is not None:
        # the sketch state reads every row, the profile a sample: merged, they would disagree
        raise ValueError("sketches and sample cannot be combined: the sketch state reads every row")
    if decisions is not None:
        from shape.proposals import decided_types

        fmt = replace(fmt, table_types=decided_types(decisions))
    # inf / NaN inputs are data, not numpy warnings
    with np.errstate(all="ignore"), single_threaded_pools():
        if is_workbook_spec(source):
            if validators:
                raise ValueError(
                    "validators apply to a single table or a dict of tables, not a workbook"
                )
            from .workbook import profile_workbook

            _refuse_workbook_options(
                version=version,
                as_of=as_of,
                delimiter=delimiter,
                encoding=encoding,
                quotechar=quotechar,
                header=None if header else header,
            )
            data, title = profile_workbook(
                source,
                name,
                sheet,
                include_hidden,
                joint,
                spec,
                univariate=univariate,
                reference_pairs=reference_pairs,
                multivariate=multivariate,
                columns=selected,
                exclude=excluded,
            )
            if sketches:
                raise SourceError("sketches are not kept for .xlsx workbooks")
            return Profile(data, name=title)
        if sheet is not None or include_hidden:
            raise SourceError("sheet and include_hidden apply to .xlsx workbooks only")
        return _profile(
            source,
            name,
            version,
            as_of,
            fmt,
            reference_pairs,
            joint,
            sketches,
            univariate,
            spec,
            validators,
            multivariate,
            time_column,
            selected,
            excluded,
        )


def _mark_univariate(cols: list[Any], on: bool, multivariate: bool = False) -> None:
    """Ask for the opt-in depth: W3-07's univariate fields per column, W3-08's multivariate
    entries of the table's joint analysis (read from its columns)."""
    for c in cols:
        c.univariate = on
        c.multivariate = multivariate


def _refuse_workbook_options(**given: Any) -> None:
    """A workbook has no Delta versions and no CSV format: refuse those options by name (#319),
    as a CSV source refuses ``sheet=``."""
    for option, value in given.items():
        if value is not None:
            raise SourceError(
                f"{option} does not apply to an .xlsx workbook (it reads a Delta table or a CSV "
                "file); leave it out"
            )


def _load_tables(
    sources: dict[str, Any], csv: CsvFormat | None = None
) -> dict[str, tuple[list[Any], int]]:
    """Read every table, concurrently when there are several (the readers release the GIL), so
    the small tables' reads hide behind the largest one's. Errors surface in table order."""
    if len(sources) == 1:
        ((name, src),) = sources.items()
        _, cols, rows = load_columns(src, name, None, csv)
        _warn_delimiter(name, src, cols, csv)
        return {name: (cols, rows)}
    with ThreadPoolExecutor(max_workers=len(sources)) as ex:
        futures = [(n, ex.submit(load_columns, src, n, None, csv)) for n, src in sources.items()]
        loaded = [(n, f.result()) for n, f in futures]
    for n, (_, cols, _rows) in loaded:
        _warn_delimiter(n, sources[n], cols, csv)
    return {n: (cols, rows) for n, (_, cols, rows) in loaded}


def _warn_delimiter(name: str, src: Any, cols: list[Any], csv: CsvFormat | None) -> None:
    """A CSV that came out as one column whose name holds a likely delimiter was probably split
    on the wrong one: say so instead of profiling it silently."""
    if len(cols) != 1 or not isinstance(src, (str, Path)) or not str(src).lower().endswith(".csv"):
        return
    col_name = str(cols[0].name)
    found = [d for d in (";", "\t", "|", ",") if d in col_name]
    if found:
        warnings.warn(
            f"{name!r} was read as one column called {col_name!r}, which contains "
            f"{found[0]!r}: the file may use that delimiter. Pass delimiter={found[0]!r} "
            "(shape profile --delimiter).",
            UserWarning,
            stacklevel=_caller_stacklevel(),
        )


_PACKAGE_DIR = str(Path(__file__).resolve().parents[2])  # .../shape


def _caller_stacklevel() -> int:
    """The ``stacklevel`` that points a warning at the first frame outside the ``shape`` package
    (the caller's line), however many internal frames lie between (#325)."""
    import sys

    frame = sys._getframe(1)
    level = 1
    while frame is not None and frame.f_code.co_filename.startswith(_PACKAGE_DIR):
        frame = frame.f_back  # type: ignore[assignment]
        level += 1
    return level


def check_reference_pairs(specs: Any, cols: list[Any]) -> None:
    """Refuse a malformed ``reference_pairs`` list (one table's) before the table is profiled,
    so a typo does not cost a whole profile (#325). The references themselves are read later."""
    if not specs:
        return
    if not isinstance(specs, (list, tuple)):
        raise ValueError("reference_pairs is a list of {columns, reference} objects")
    from shape.profile.joint.reference import _mapping

    names = {c.name for c in cols}
    for spec in specs:
        if not isinstance(spec, Mapping):
            raise ValueError("reference_pairs is a list of {columns, reference} objects")
        for key in ("columns", "reference"):
            if key not in spec:
                raise ValueError(f"a reference pair needs {key!r}: {dict(spec)!r}")
        absent = [c for c in _mapping(spec["columns"]) if c not in names]
        if absent:
            raise ValueError(f"reference pair: the data has no column {absent[0]!r}")


def check_reference_tables(specs: Any, cols_by_t: dict[str, tuple[list[Any], int]]) -> None:
    """``check_reference_pairs`` for several tables: a dict of table name to list."""
    if not specs:
        return
    if not isinstance(specs, Mapping):
        raise ValueError("for several tables, reference_pairs maps a table name to a list")
    for tname, table_specs in specs.items():
        if tname not in cols_by_t:
            raise ValueError(f"reference_pairs names the table {tname!r}, which is not here")
        check_reference_pairs(table_specs, cols_by_t[tname][0])


def attach_reference_pairs(doc: dict[str, Any], cols: list[Any], rows: int, specs: Any) -> None:
    """Add the reference-pair measurements to a table document's ``joint`` entry."""
    if not specs:
        return
    from shape.profile.joint.reference import measure_reference_pairs

    measured = measure_reference_pairs(cols, rows, specs)
    joint = doc.get("joint")
    if joint is None:
        joint = doc["joint"] = {"version": 1}
    joint["reference_pairs"] = measured
    record = doc.get("sampling")
    if record is not None and any(m.get("sampled") for m in measured):
        # the check ran on an evenly spread part of a larger table (on the whole table, not on a
        # sample of the caller's): the profile says so
        from shape.profile.joint import reference as _pairs

        record["internal"].append(_sampling.internal_entry("reference_pairs", _pairs.MAX_ROWS))


def _check_selection(
    known: set[str], columns: tuple[str, ...] | None, exclude: tuple[str, ...]
) -> None:
    for flag, names in (("--columns", columns or ()), ("--exclude", exclude)):
        for name in names:
            if name not in known:
                raise ValueError(
                    f"{flag} {name!r} is not a column of the data "
                    f"(columns: {', '.join(sorted(known))})"
                )


def _select_columns(
    cols: list[Any], include: tuple[str, ...] | None, exclude: tuple[str, ...]
) -> list[Any]:
    return [c for c in cols if (include is None or c.name in include) and c.name not in exclude]


def _profile(
    source: Any,
    name: str | None,
    version: int | None,
    as_of: Any,
    csv: CsvFormat | None = None,
    reference_pairs: Any = None,
    joint: bool | None = None,
    sketches: bool = False,
    univariate: bool = False,
    sample: _sampling.SampleSpec | None = None,
    validators: Any = None,
    multivariate: bool = False,
    time_column: str | None = None,
    columns: tuple[str, ...] | None = None,
    exclude: tuple[str, ...] = (),
) -> Profile:
    check_delta_options(version, as_of)
    asked = version is not None or as_of is not None
    if isinstance(source, dict):
        if asked:
            raise SourceError("version and as_of read one Delta table, not a dict of tables")
        if not source:
            raise SourceError("an empty dict of tables cannot be profiled")
        named = {str(k): v for k, v in source.items()}
        cols_by_t = _load_tables(named, csv)
        known = {c.name for cols, _ in cols_by_t.values() for c in cols}
        _check_selection(known, columns, exclude)
        cols_by_t = {
            n: (_select_columns(cols, columns, exclude), rows)
            for n, (cols, rows) in cols_by_t.items()
        }
        if time_column is not None and not any(
            c.name == time_column for cols, _ in cols_by_t.values() for c in cols
        ):
            raise ValueError(f"time_column {time_column!r} is not a column of any table")
        check_reference_tables(reference_pairs, cols_by_t)
        for cols, _rows in cols_by_t.values():
            _mark_univariate(cols, univariate, multivariate)
        doc = dataset_to_dict(profile_dataset_columns(cols_by_t, None, joint, sample, time_column))
        for tname, specs in (reference_pairs or {}).items():
            cols, rows = cols_by_t[tname]
            attach_reference_pairs(doc["tables"][tname], cols, rows, specs)
        if validators:
            if not isinstance(validators, dict) or not all(
                isinstance(v, dict) for v in validators.values()
            ):
                raise ValueError(
                    "for several tables, validators maps a table name to a {column: kinds} dict"
                )
            from shape.validation.valid_as import attach_validators

            for tname, specs in validators.items():
                if tname not in cols_by_t:
                    raise ValueError(f"validators names the table {tname!r}, which is not here")
                attach_validators(doc["tables"][tname], cols_by_t[tname][0], specs)
        return Profile(
            doc,
            name=name,
            sketches=_sketch_state(
                named, csv, {n: [c.name for c in cols] for n, (cols, _) in cols_by_t.items()}
            )
            if sketches
            else None,
        )
    delta = delta_dir(source)
    if delta is None:
        if asked:
            raise SourceError(
                "version and as_of read a Delta table: the source is not a Delta table"
            )
        table_name, cols, rows = load_columns(source, name, None, csv)
        _warn_delimiter(table_name, source, cols, csv)
        provenance = None
        sketch_source = source
    else:
        table, provenance = read_delta(delta, version=version, as_of=as_of)
        table_name, cols, rows = load_columns(table, name or delta.name)
        sketch_source = table
    _check_selection({c.name for c in cols}, columns, exclude)
    cols = _select_columns(cols, columns, exclude)
    _mark_univariate(cols, univariate, multivariate)
    check_reference_pairs(reference_pairs, cols)
    if time_column is not None and all(c.name != time_column for c in cols):
        raise ValueError(f"time_column {time_column!r} is not a column of {table_name!r}")
    table_profile = _profile_cols_table(
        table_name, cols, rows, None, None, joint, sample, time_column
    )
    doc = table_to_dict(table_profile)
    attach_reference_pairs(doc, cols, rows, reference_pairs)
    if validators:
        from shape.validation.valid_as import attach_validators

        attach_validators(doc, cols, validators)
    return Profile(
        doc,
        name=name,
        provenance=provenance,
        sketches=_sketch_state(
            {table_name: sketch_source}, csv, {table_name: [c.name for c in cols]}
        )
        if sketches
        else None,
    )


def _sketch_state(
    sources: dict[str, Any], csv: CsvFormat | None, columns: Mapping[str, list[str]] | None = None
) -> dict[str, Any]:
    from shape.profile import sketches  # only when asked for

    return sketches.build_document(sources, csv, columns)


# --- .shape artifact ---------------------------------------------------------------


def _encode(data: dict[str, Any]) -> bytes:
    # No sort_keys: the order of enum_values / value_counts_ext is meaningful.
    return codec.dumps(data, sort_keys=False)


def save(
    p: Profile,
    path: str | Path,
    capture: str = "safe",
    *,
    k: int = 5,
    column_k: Mapping[str, int] | None = None,
    classifications: Mapping[str, str] | None = None,
    vault: str | Path | None = None,
    vault_policy: Any = None,
    kek: Any = None,
) -> str:
    """Write ``p`` to a ``.shape`` artifact and return its content id (sha256).

    ``capture`` chooses what is written. ``"safe"`` (the default) keeps statistics and formats
    only for a sensitive column and category values only where every category has at least ``k``
    rows (``column_k`` sets ``k`` for one column; ``classifications`` maps a column to its declared
    classification, and ``CONFIDENTIAL`` or higher makes it sensitive). ``"full"`` keeps real
    values, and the artifact says so; do not share it. ``p`` itself is not changed. A profile that
    was captured safe cannot be saved as full.

    ``vault`` (a path) also writes the value vault of what the safe capture withheld, chosen by
    ``vault_policy`` (a policy file, document or ``shape.vault.policy.VaultPolicy``) and encrypted
    under ``kek`` (32 bytes, or a credential reference such as ``file://KEK.key``); the vault is
    written first, and the artifact's manifest names it by ``vault_id`` and SHA-256 (see
    ``docs/VAULT.md``). It needs ``capture="safe"`` and a profile that holds real values.
    """
    from shape.privacy.redact import CaptureConfig, redact_profile

    if not isinstance(p, Profile):
        raise TypeError(f"save() expects a Profile, got {type(p).__name__}")
    out = redact_profile(
        p,
        CaptureConfig(
            mode=capture, k=k, column_k=column_k or {}, classifications=classifications or {}
        ),
    )
    if vault is None:
        if vault_policy is not None or kek is not None:
            raise ValueError("vault_policy and kek apply to a vault: pass vault=PATH")
        return save_captured(out, path)
    if capture != "safe":
        raise ValueError(
            "a vault needs capture safe: with capture full the values are already in the clear"
        )
    return save_with_vault(p, out, path, vault, vault_policy, kek, classifications)


def save_with_vault(
    full: Profile,
    captured: Profile,
    path: str | Path,
    vault: str | Path,
    vault_policy: Any,
    kek: Any,
    classifications: Mapping[str, str] | None = None,
) -> str:
    """Write the safe capture ``captured`` of ``full`` and the value vault of what it withheld
    (:mod:`shape.vault`): the vault first, then the artifact whose manifest names it by
    ``vault_id`` and SHA-256. Returns the content id. Nothing is written when the policy, the key
    or a path is refused."""
    if vault_policy is None:
        raise ValueError("a vault needs vault_policy")
    if kek is None:
        raise ValueError("a vault needs kek (32 bytes or a credential reference)")
    if full.capture_declared and full.capture["mode"] == "safe":
        raise ValueError(
            "this profile was captured safe: its values are gone, so there is nothing for a "
            "vault; profile the data again"
        )
    from shape.vault.build import write_profile_vault

    body = _encode(captured._data)
    written = write_profile_vault(
        full,
        captured,
        hashlib.sha256(body).hexdigest(),
        vault,
        vault_policy,
        kek,
        classifications,
    )
    try:
        return save_captured(captured, path, vault=written["reference"])
    except BaseException:
        Path(vault).unlink(missing_ok=True)
        raise


def save_captured(out: Profile, path: str | Path, *, vault: dict[str, str] | None = None) -> str:
    """Write a profile that already carries how it was captured (see
    :func:`shape.privacy.redact.redact_profile`) and return its content id. ``vault`` is the
    ``{"vault_id", "sha256"}`` reference to record in the manifest."""
    if not out.capture_declared:
        raise ValueError("this profile does not say how it was captured: use save()")
    body = _encode(out._data)
    content_id = hashlib.sha256(body).hexdigest()
    manifest: dict[str, Any] = compat.stamp(
        "profile-artifact",
        {
            "kind": ARTIFACT_KIND,
            "name": out.name,
            "shape_content_id": content_id,
            "capture": out.capture,
        },
    )
    if out.redaction_manifest:
        manifest["redaction_manifest"] = out.redaction_manifest
    if vault is not None:
        manifest["vault"] = dict(vault)
    if out.provenance is not None:
        manifest["provenance"] = out.provenance
    components = {PROFILE_COMPONENT: body}
    if out._sketches is not None:
        from shape.profile import sketches

        manifest["sketches"] = {"format": sketches.FORMAT, "version": sketches.VERSION}
        components[sketches.COMPONENT] = codec.dumps(out._sketches, sort_keys=True)
    write_artifact(str(path), manifest, components)
    return content_id


def check_capture(value: Any) -> dict[str, Any]:
    """The ``capture`` record of an artifact manifest, checked; ``ValueError`` when malformed."""
    if not isinstance(value, dict):
        raise ValueError(f"capture must be an object, not {type(value).__name__}")
    mode, k = value.get("mode"), value.get("k")
    if mode not in ("safe", "full"):
        raise ValueError(f"capture mode must be 'safe' or 'full', not {mode!r}")
    if mode == "safe" and (isinstance(k, bool) or not isinstance(k, int) or k < 1):
        raise ValueError(f"a safe capture needs k, an integer of at least 1, not {k!r}")
    if mode == "full" and k is not None:
        raise ValueError(f"a full capture has no k, not {k!r}")
    return dict(value)


def load(path: str | Path) -> Profile:
    """Read a ``.shape`` artifact written by :func:`save`."""
    manifest, parts = read_artifact(str(path))
    if manifest.get("format") != ARTIFACT_FORMAT or manifest.get("kind") != ARTIFACT_KIND:
        raise ArtifactError(f"{path} is not a Shape profile artifact")
    compat.check_readable("profile-artifact", manifest, error=ArtifactError)
    body = parts.get(PROFILE_COMPONENT)
    if body is None:
        raise ArtifactError("profile component missing")
    if hashlib.sha256(body).hexdigest() != manifest.get("shape_content_id"):
        raise ArtifactError("Shape content identity mismatch")
    try:
        data = codec.loads(body)
        validate_structure(data, allow_nonfinite=True)  # depth and size bounds, as read_model
    except (ValueError, TypeError, RecursionError) as e:
        raise ArtifactError(f"invalid {PROFILE_COMPONENT}: {e}") from e
    if not isinstance(data, dict):
        raise ArtifactError(f"invalid {PROFILE_COMPONENT}: not an object")
    if "merge" in data:
        from shape.profile.merge import MergeError, check_block

        try:
            check_block(data["merge"])
        except MergeError as e:
            raise ArtifactError(f"invalid {PROFILE_COMPONENT}: {e}") from e
    provenance = manifest.get("provenance")
    capture = None
    if "capture" in manifest:
        try:
            capture = check_capture(manifest["capture"])
        except ValueError as e:
            raise ArtifactError(f"invalid capture in the manifest of {path}: {e}") from e
    redaction = manifest.get("redaction_manifest")
    return Profile(
        data,
        name=str(manifest.get("name") or "") or None,
        provenance=provenance if isinstance(provenance, dict) else None,
        sketches=_load_sketches(manifest, parts),
        capture=capture,
        redaction_manifest=redaction if isinstance(redaction, dict) else None,
    )


def _load_sketches(manifest: dict[str, Any], parts: dict[str, Any]) -> dict[str, Any] | None:
    from shape.profile import sketches

    body = parts.get(sketches.COMPONENT)
    if body is None:
        if manifest.get("sketches") is not None:
            raise ArtifactError(f"{sketches.COMPONENT} is named in the manifest but missing")
        return None
    try:
        doc = codec.loads(body)
        validate_structure(doc, allow_nonfinite=False)
        return sketches.validate(doc)
    except (ValueError, TypeError, RecursionError) as e:  # SketchStateError is a ValueError
        raise ArtifactError(f"invalid {sketches.COMPONENT}: {e}") from e
