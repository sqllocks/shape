"""``shape mask``: replace personal data in data files with synthetic values (P6-03).

Nothing heavy loads at import time (T-18); the command imports Arrow, the profiler and the
masker when it runs.
"""

from __future__ import annotations

import argparse
import errno
import json
from pathlib import Path
from typing import Any

FORMATS = ("csv", "parquet")


def add_arguments(sub: Any) -> None:
    ma = sub.add_parser(
        "mask",
        help="replace personal data in data files with synthetic values",
        description="Find columns that hold personal data (e-mail, phone, names, addresses, "
        "SSN, card numbers, IP addresses, IBANs, dates of birth, ...) from their names and their "
        "values, and replace them with synthetic values in the same format. Null positions and "
        "every other column are kept exactly; the same original value gets the same replacement "
        "everywhere, so keys and the columns that refer to them still match.",
    )
    ma.add_argument("input", metavar="PATH", help="a data file, or a directory of data files")
    ma.add_argument(
        "-o", "--output", required=True, metavar="DIR", help="directory for the masked files"
    )
    ma.add_argument(
        "--format",
        dest="input_format",
        choices=FORMATS,
        help="the file format of a directory's files and of the output (default: from the "
        "input file's extension; csv for a directory)",
    )
    ma.add_argument("--seed", type=int, default=42, help="random seed (default 42)")
    ma.add_argument(
        "--exclude", action="append", default=[], metavar="COLUMN", help="leave this column as is"
    )
    ma.add_argument(
        "--pii",
        action="append",
        default=[],
        metavar="COLUMN=TYPE",
        help="mask this column as TYPE even if nothing marks it as personal data",
    )
    ma.add_argument("--json", action="store_true", help="print the summary as JSON")


def _files(path: Path, fmt: str | None) -> tuple[dict[str, Path], str]:
    if path.is_dir():
        fmt = fmt or "csv"
        files = sorted(path.glob(f"*.{fmt}"))
        if not files:
            raise ValueError(f"no {fmt} files found in {path}")
        return {p.stem: p for p in files}, fmt
    if not path.is_file():
        raise FileNotFoundError(errno.ENOENT, "file not found", str(path))
    ext = path.suffix.lower().lstrip(".")
    fmt = fmt or ext
    if fmt not in FORMATS or ext != fmt:
        raise ValueError(f"{path} is not a {fmt if fmt in FORMATS else 'csv or parquet'} file")
    return {path.stem: path}, fmt


def _pii(pairs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in pairs:
        column, sep, type_name = pair.rpartition("=")
        if not sep or not column or not type_name:
            raise ValueError(f"--pii needs COLUMN=TYPE, got {pair!r}")
        out[column] = type_name
    return out


def _read(path: Path, fmt: str) -> Any:
    """The file as an Arrow table, CSV values as text (so unmasked columns are written back
    exactly as they were read)."""
    import pyarrow.csv as pacsv  # type: ignore[import-untyped]
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    if fmt == "parquet":
        return pq.read_table(path)
    names = pacsv.open_csv(path).schema.names
    import pyarrow as pa

    return pacsv.read_csv(
        path,
        convert_options=pacsv.ConvertOptions(
            column_types={n: pa.string() for n in names},
            strings_can_be_null=True,
            null_values=[""],
        ),
    )


def run(a: argparse.Namespace) -> int:
    import pyarrow.csv as pacsv
    import pyarrow.parquet as pq

    from shape.plugins.host import default_host
    from shape.profile.reference import profile

    files, fmt = _files(Path(a.input), a.input_format)
    out_dir = Path(a.output)
    for p in files.values():
        if (out_dir / p.name).resolve() == p.resolve():
            raise ValueError("the output directory would overwrite the input files")
    pii = _pii(a.pii)

    tables = {name: _read(p, fmt) for name, p in files.items()}
    typed = profile({name: str(p) for name, p in files.items()}).to_dict()
    transform = default_host().get("shape.transforms", "mask")
    result = transform.mask(tables, seed=a.seed, exclude=a.exclude, pii=pii, profile=typed)

    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, table in result.tables.items():
        target = out_dir / f"{name}.{fmt}"
        if fmt == "csv":
            pacsv.write_csv(table, target)
        else:
            pq.write_table(table, target)
        written.append(str(target))

    if a.json:
        print(
            json.dumps(
                {
                    "files": written,
                    "columns_masked": result.columns_masked,
                    "column_types": result.column_types,
                    "stats": result.stats,
                },
                indent=2,
            )
        )
        return 0
    for name, table in tables.items():
        print(f"  Read {name}: {table.num_rows:,} rows x {table.num_columns} columns")
    print()
    print(result.summary())
    print()
    print(f"Written {len(written)} {fmt.upper()} files to {out_dir}/")
    return 0
