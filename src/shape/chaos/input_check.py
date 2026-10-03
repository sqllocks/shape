"""Chaos corrupts only data that Shape wrote (``docs/CHAOS.md``).

``shape chaos --input DIR`` accepts a table file when ``DIR/_shape_provenance.json`` lists it with
a matching SHA-256, or when it is a Parquet file with the ``shape_synthetic`` metadata key. A
hand-written file, or a generated one that was edited afterwards, is refused before anything is
written, unless the user passes ``--allow-real-input``. The check is provenance only; it does not
look at the data, and it stops a mistake, not a determined user (``docs/THREAT_MODEL.md``).
"""

from __future__ import annotations

from pathlib import Path

from shape.io.provenance import CHANGED, IGNORED_FILES, VERIFIED, verify_file

TABLE_SUFFIXES = (".csv", ".parquet", ".jsonl")
VERIFIED_RUN = "verified"
UNVERIFIED_RUN = "unverified"
OVERRIDE_WARNING = (
    "shape: warning: --allow-real-input: corrupting data that is not marked as Shape-generated"
)


class ChaosInputError(ValueError):
    """A chaos input or output the command refuses (exit 2)."""


def table_files(directory: str | Path) -> list[Path]:
    """The table files ``shape chaos --input`` reads from ``directory`` (the sidecar and
    ``_SUCCESS`` are not tables)."""
    folder = Path(directory)
    return sorted(
        p
        for suffix in TABLE_SUFFIXES
        for p in folder.glob(f"*{suffix}")
        if p.is_file() and p.name not in IGNORED_FILES
    )


def check_output_folder(input_dir: str | Path, output_dir: str | Path) -> None:
    """``-o`` must not be the input folder or inside it: input files are never overwritten."""
    source = Path(input_dir).resolve()
    target = Path(output_dir).resolve()
    if target == source or source in target.parents:
        raise ChaosInputError(f"chaos output {output_dir} is the input folder; give a different -o")


def verify_chaos_input(directory: str | Path, *, allow_real_input: bool = False) -> str:
    """``"verified"`` when every table file of ``directory`` is marked as Shape-generated. A file
    that is not raises :class:`ChaosInputError`, unless ``allow_real_input``, which returns
    ``"unverified"`` (the caller prints :data:`OVERRIDE_WARNING`)."""
    unverified = False
    for path in table_files(directory):
        status = verify_file(path)
        if status == VERIFIED:
            continue
        if not allow_real_input:
            message = (
                f"chaos input {path} is not marked as Shape-generated data; chaos only "
                "corrupts synthetic data (pass --allow-real-input to override)"
            )
            if status == CHANGED:
                message += " (its sha256 differs from the provenance record)"
            raise ChaosInputError(message)
        unverified = True
    return UNVERIFIED_RUN if unverified else VERIFIED_RUN


__all__ = [
    "OVERRIDE_WARNING",
    "ChaosInputError",
    "check_output_folder",
    "table_files",
    "verify_chaos_input",
]
