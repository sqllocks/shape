"""The profile and the data a run looks at, normalised."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pyarrow as pa  # type: ignore[import-untyped]

    from shape.profile.reference.model import DatasetProfile

DataSource = Any  # a pyarrow Table, a .csv or .parquet path, or a table-name mapping of those
_SUFFIXES = (".csv", ".parquet")


def dataset_of(profile: Any) -> DatasetProfile:
    """A multi-table or single-table profile (a ``Profile``, its dict, or a ``.shape`` path)."""
    if isinstance(profile, (str, Path)):
        import shape

        profile = shape.load(str(profile))
    from shape.generation.learn import as_dataset

    return as_dataset(profile)


def _read(source: Any) -> pa.Table:
    import pyarrow as pa
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    if isinstance(source, pa.Table):
        return source
    if isinstance(source, (str, Path)):
        path = Path(source)
        if path.suffix == ".csv":
            from shape.profile.reference.readers import read_csv

            return read_csv(path)
        if path.suffix == ".parquet":
            return pq.read_table(path)
        raise ValueError(f"cannot read {path} as table data: expected .csv or .parquet")
    raise ValueError(
        f"table data must be a pyarrow Table or a file path, not {type(source).__name__}"
    )


def load_data(data: DataSource | None, dataset: DatasetProfile) -> dict[str, pa.Table]:
    """The tables of ``data`` by name, checked against the profile.

    ``data`` is a mapping of table name to a table or file, or a directory with one
    ``NAME.csv`` or ``NAME.parquet`` per table. Every table must be one the profile has, and
    hold every column the profile has for it."""
    if data is None:
        return {}
    if isinstance(data, (str, Path)):
        folder = Path(data)
        if not folder.is_dir():
            raise ValueError(f"{folder} is not a directory (give a directory or NAME=PATH pairs)")
        found: dict[str, Path] = {}
        for path in sorted(folder.iterdir()):
            if path.suffix in _SUFFIXES and path.stem in dataset.tables:
                found.setdefault(path.stem, path)
        data = found
    if not isinstance(data, Mapping):
        raise ValueError("data must be a mapping of table name to table or file, or a directory")
    out: dict[str, pa.Table] = {}
    for name, source in data.items():
        if name not in dataset.tables:
            raise ValueError(f"data for table {name!r}, which the profile does not have")
        table = _read(source)
        missing = [c for c in dataset.tables[name].columns if c not in table.column_names]
        if missing:
            raise ValueError(
                f"the data for table {name!r} has no column {missing[0]!r}, "
                "which its profile has: is it the data that was profiled?"
            )
        out[name] = table
    return out
