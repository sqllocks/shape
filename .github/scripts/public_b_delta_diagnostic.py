"""Bounded diagnosis of the existing corrupt Delta regression; no repository edits."""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
import tempfile
import warnings
from pathlib import Path

import deltalake
import pyarrow as pa
from deltalake import DeltaTable, write_deltalake

import shape
from shape.io import deltafs


def emit(kind, **fields):
    print(json.dumps({"kind": kind, **fields}, default=str, sort_keys=True), flush=True)


def membership(root, corrupt):
    table = DeltaTable(str(root))
    active = {Path(uri).name for uri in table.file_uris()}
    return {
        "snapshot_version": table.version(),
        "disk_files": len(list(root.glob("*.parquet"))),
        "active_files": len(active),
        "corrupt_active": corrupt.name in active,
        "corrupt_size": corrupt.stat().st_size,
        "corrupt_sha256": hashlib.sha256(corrupt.read_bytes()).hexdigest(),
        "active_names_sha256": hashlib.sha256("\n".join(sorted(active)).encode()).hexdigest(),
    }


def main():
    emit("runtime", python=platform.python_version(), platform=sys.platform,
         arrow=pa.__version__, deltalake=deltalake.__version__, shape_source=shape.__file__)
    with tempfile.TemporaryDirectory(prefix="shape-delta-windows-") as folder:
        root = Path(folder) / "d"
        output = Path(folder) / "d.shape"
        for i in range(30):
            part = pa.table({"id": list(range(i * 1000, (i + 1) * 1000)), "s": ["v"] * 1000})
            write_deltalake(str(root), part, mode="append")
        files = sorted(root.glob("*.parquet"))
        if len(files) != 30:
            emit("setup_unexpected_file_count", count=len(files))
        corrupt = files[15]
        corrupt.write_bytes(b"PAR1 not a parquet file")
        emit("fixture", expected_rows=30000, **membership(root, corrupt))
        for attempt in range(60):
            output.unlink(missing_ok=True)
            emit("before_cli", attempt=attempt, **membership(root, corrupt))
            try:
                done = subprocess.run(
                    [sys.executable, "-m", "shape.cli.main", "profile", str(root), "-o", str(output)],
                    capture_output=True, text=True, timeout=60, check=False,
                )
            except subprocess.TimeoutExpired as exc:
                emit("cli_timeout", attempt=attempt, timeout=60,
                     stdout=exc.stdout, stderr_tail=(exc.stderr or b"")[-4000:])
                continue
            emit("cli_result", attempt=attempt, returncode=done.returncode,
                 stdout=done.stdout, stderr_tail=done.stderr[-4000:], output_exists=output.exists())
            if done.returncode == 0 and output.exists():
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        profile = shape.load(output)
                    emit("unexpected_success_profile", attempt=attempt,
                         tables={name: table.get("row_count") for name, table in profile.tables.items()})
                except Exception as exc:
                    emit("unexpected_success_artifact_error", attempt=attempt,
                         error_type=type(exc).__name__, message=str(exc))
        for threaded in (True, False):
            for attempt in range(2):
                emit("before_arrow", attempt=attempt, use_threads=threaded,
                     **membership(root, corrupt))
                try:
                    result = deltafs.local_dataset(DeltaTable(str(root)), root).to_table(use_threads=threaded)
                except Exception as exc:
                    emit("arrow_result", attempt=attempt, use_threads=threaded,
                         error_type=type(exc).__name__, message=str(exc)[-4000:])
                else:
                    emit("arrow_result", attempt=attempt, use_threads=threaded,
                         rows=result.num_rows, expected_rows=30000)
    emit("complete")


if __name__ == "__main__":
    main()
