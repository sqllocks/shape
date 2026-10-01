"""The optional CTGAN model (``pip install 'sqllocks-shape[ctgan]'``): a deep generative model
for one table, through the ``sdv`` library, as the Python class :class:`CtganModel` and the
command ``shape ctgan``.

Nothing here imports ``sdv`` until a model is fitted, so the plugin loads and lists fine without
it: :meth:`CtganModel.is_available` says whether it is installed, and fitting without it raises an
``ImportError`` that names the extra. ``shape ctgan`` without it prints the same advice and exits 2.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

SHAPE_API = "1.0"

INSTALL_HINT = "pip install 'sqllocks-shape[ctgan]'"


class CtganModel:
    """CTGAN, fitted on one Arrow table and sampled into another.

    ``epochs`` and ``batch_size`` are passed to ``sdv``'s ``CTGANSynthesizer`` (its batch size
    must be a multiple of 10). Training is slow and uses the CPU unless sdv finds a GPU."""

    def __init__(self, epochs: int = 300, batch_size: int = 500) -> None:
        self.epochs = epochs
        self.batch_size = batch_size
        self._model: Any = None

    @staticmethod
    def is_available() -> bool:
        """Whether ``sdv`` can be imported."""
        try:
            import sdv.single_table  # noqa: F401, PLC0415
        except ImportError:
            return False
        return True

    def fit(self, table: pa.Table, discrete_columns: list[str] | None = None) -> CtganModel:
        """Fit the model on ``table``. Columns that are not numbers are treated as categorical,
        and so are ``discrete_columns`` (replacing that default when given)."""
        try:
            from sdv.metadata import SingleTableMetadata  # noqa: PLC0415
            from sdv.single_table import CTGANSynthesizer  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(f"CTGAN needs the sdv library: {INSTALL_HINT}") from exc
        frame = table.to_pandas()
        discrete = (
            discrete_columns
            if discrete_columns is not None
            else [
                name
                for name, t in zip(table.column_names, table.schema.types, strict=True)
                if not (pa.types.is_integer(t) or pa.types.is_floating(t))
            ]
        )
        unknown = [c for c in discrete if c not in table.column_names]
        if unknown:
            raise ValueError(f"discrete columns not in the table: {unknown}")
        metadata = SingleTableMetadata()
        metadata.detect_from_dataframe(frame)
        for name in discrete:
            metadata.update_column(column_name=name, sdtype="categorical")
        model = CTGANSynthesizer(metadata, epochs=self.epochs, batch_size=self.batch_size)
        model.fit(frame)
        self._model = model
        return self

    def sample(self, n_rows: int) -> pa.Table:
        """``n_rows`` synthetic rows as an Arrow table."""
        if self._model is None:
            raise RuntimeError("CtganModel must be fitted before sampling")
        return pa.Table.from_pandas(self._model.sample(num_rows=n_rows), preserve_index=False)


class CtganCommand:
    """``shape ctgan DATA -n ROWS -o OUT``: fit CTGAN on a table and write synthetic rows."""

    name = "ctgan"
    help = "fit a CTGAN model on a table and sample synthetic rows (needs the [ctgan] extra)"

    def configure(self, parser: Any) -> None:
        parser.add_argument("data", metavar="DATA", help="a CSV, Parquet or JSONL file")
        parser.add_argument("-n", "--rows", type=int, default=1000, help="rows to sample")
        parser.add_argument("-o", "--output", required=True, metavar="OUT", help="Parquet file")
        parser.add_argument("--epochs", type=int, default=300)
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument(
            "--discrete",
            metavar="COLUMNS",
            help="comma-separated categorical columns (default: every non-numeric column)",
        )
        parser.add_argument("--input-format", default="auto", choices=("auto", "csv", "parquet", "jsonl"))

    def run(self, args: argparse.Namespace) -> int:
        if not CtganModel.is_available():
            print(f"shape: CTGAN needs the sdv library: {INSTALL_HINT}", file=sys.stderr)
            return 2
        import pyarrow.parquet as pq  # noqa: PLC0415

        from shape.quality import load_tables  # noqa: PLC0415

        tables = load_tables(args.data, args.input_format)
        if len(tables) != 1:
            print("shape: ctgan fits one table; DATA must be one file", file=sys.stderr)
            return 2
        table = next(iter(tables.values()))
        discrete = [c for c in args.discrete.split(",") if c] if args.discrete else None
        model = CtganModel(args.epochs, args.batch_size).fit(table, discrete)
        pq.write_table(model.sample(args.rows), args.output)
        return 0


__all__ = ["SHAPE_API", "CtganCommand", "CtganModel"]
