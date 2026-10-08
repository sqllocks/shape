"""Rolling, atomically published files for one table (the engine behind the file sinks' micro-batch
mode and the ``abfss://`` sink).

``RollingTableWriter`` takes batches and writes them as a series of files in a
:class:`~shape.io.store.Store`. A file is *rolled* (completed, published under its final name, and
a new one started) after ``roll_rows`` rows or ``roll_seconds`` seconds, and on :meth:`flush`, so a
reader sees the rows of a stream while it runs. Nothing is visible before it is complete: see
:mod:`shape.io.store`. With neither threshold the table is one file, published on :meth:`close`.

Rolling by rows is exact (a batch is split at the boundary), so the files of a seeded run are the
same files every time; rolling by seconds depends on the clock and is for live streams.

File names come from a path template (:mod:`shape.io.landing`); a rolling template has ``{part}``.
``mode`` is ``overwrite`` (replace a file of the same name), ``fail`` (error if the first file
already exists) or ``append`` (continue after the highest existing part).
"""

from __future__ import annotations

import datetime as dt
import re
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import PurePosixPath
from typing import Any, Protocol

import pyarrow as pa  # type: ignore[import-untyped]

from shape.io.landing import check_template, render_path, uses_date
from shape.io.store import Store

MODES = ("overwrite", "append", "fail")
_PART_MARK = "\ue000"  # stands for {part} while a name pattern is built (a private-use character)
ROLL_OPTIONS = ("roll_rows", "roll_seconds")


class Encoder(Protocol):
    extension: str

    def _open(self, target: Any, schema: pa.Schema, options: dict[str, Any]) -> Any: ...

    def _write(self, writer: Any, batch: pa.RecordBatch) -> None: ...


def wants_rolling(options: Mapping[str, Any]) -> bool:
    return any(options.get(k) for k in ROLL_OPTIONS)


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class RollingTableWriter:
    def __init__(
        self,
        store: Store,
        table: str,
        encoder: Encoder,
        options: Mapping[str, Any],
        *,
        template: str,
    ) -> None:
        self._store = store
        self._table = table
        self._encoder = encoder
        self._options = dict(options)
        check_template(template)
        self._template = template
        self.roll_rows = int(options["roll_rows"]) if options.get("roll_rows") else None
        self.roll_seconds = float(options["roll_seconds"]) if options.get("roll_seconds") else None
        if (self.roll_rows is not None and self.roll_rows < 1) or (
            self.roll_seconds is not None and self.roll_seconds <= 0
        ):
            raise ValueError("roll_rows and roll_seconds must be positive")
        self._rolling = self.roll_rows is not None or self.roll_seconds is not None
        if self._rolling and "{part}" not in template:
            raise ValueError(f"rolling files need {{part}} in the path template, got {template!r}")
        self._mode = str(options.get("mode") or "overwrite")
        if self._mode not in MODES:
            raise ValueError(f"mode is one of {', '.join(MODES)}, got {self._mode!r}")
        if self._mode == "append" and "{part}" not in template:
            raise ValueError(
                f"mode=append adds files, so the path template needs {{part}}, got {template!r}"
            )
        self._batch_date = options.get("batch_date")
        self._clock: Callable[[], float] = options.get("clock") or time.monotonic
        self._now: Callable[[], dt.datetime] = options.get("now") or _utc_now
        self._manifest = bool(options.get("manifest"))
        self._part = 0
        self._pending: Any = None
        self._writer: Any = None
        self._file_rows = 0
        self._opened_at = 0.0
        self._folders: list[str] = []
        self.rows = 0
        self.files: list[str] = []
        self._rels: set[str] = set()
        self._first_now: dt.datetime | None = None

    # ---- naming ---------------------------------------------------------------------------

    def _render(self, part: int, now: dt.datetime) -> str:
        return self._render_template(self._template, part, now)

    def _render_template(self, template: str, part: int | None, now: dt.datetime) -> str:
        date = self._batch_date
        if date is None and uses_date(template):
            date = now.date()
        return render_path(
            template,
            self._table,
            self._encoder.extension,
            date,
            part=part if "{part}" in template else None,
            now=now,
        )

    def _part_pattern(self, now: dt.datetime) -> tuple[str, re.Pattern[str]]:
        """The folder of the first file and a pattern for the names of the files of this table
        there, with the part number as a group. The path is rendered with a marker where
        ``{part}`` stands, so a ``00001`` in the table name, the date or the time is never taken
        for the part, and a part number of six or more digits is read in full."""
        rendered = self._render_template(self._template.replace("{part}", _PART_MARK), None, now)
        path = PurePosixPath(rendered)
        folder = "" if str(path.parent) == "." else str(path.parent)
        pieces = path.name.split(_PART_MARK)
        regex = re.escape(pieces[0])
        for i, tail in enumerate(pieces[1:]):
            regex += (r"(?P<part>\d{5,})" if i == 0 else r"(?P=part)") + re.escape(tail)
        return folder, re.compile(regex)

    def _next_part(self, now: dt.datetime) -> int:
        part = self._part + 1
        if self._part == 0 and self._mode in ("append", "fail"):
            first = self._render(1, now)
            folder = str(PurePosixPath(first).parent)
            folder = "" if folder == "." else folder
            if self._mode == "fail":
                if self._store.exists(first):
                    raise FileExistsError(
                        f"{self._store.location(first)} already exists (mode=fail)"
                    )
            else:
                folder, pattern = self._part_pattern(now)
                found = [
                    int(m.group("part"))
                    for n in self._store.names(folder)
                    if (m := pattern.fullmatch(n))
                ]
                part = max(found, default=0) + 1
        return part

    # ---- writing --------------------------------------------------------------------------

    def _open(self, schema: pa.Schema) -> None:
        now = self._now()
        if self._part >= 1 and "{part}" not in self._template:
            raise ValueError(
                f"the path template {self._template!r} has no {{part}}: a second file would "
                "replace the first"
            )
        self._part = self._next_part(now)
        if self._first_now is None:
            self._first_now = now
        rel = self._render(self._part, now)
        self._pending = self._store.create(rel)
        try:
            self._writer = self._encoder._open(self._pending.handle, schema, self._options)
        except BaseException:
            self._pending.abort()
            self._pending = None
            raise
        self._file_rows = 0
        self._opened_at = self._clock()

    def _complete(self) -> None:
        pending, writer = self._pending, self._writer
        self._pending = self._writer = None
        if pending is None:
            return
        try:
            writer.close()
            pending.publish()
        except BaseException:
            pending.abort()
            raise
        self.files.append(self._store.location(pending.rel))
        self._rels.add(pending.rel)
        folder = str(PurePosixPath(pending.rel).parent) if self._manifest else ""
        if self._manifest and folder not in self._folders:
            self._folders.append(folder)

    def write_batch(self, batch: pa.RecordBatch) -> None:
        offset = 0
        total = batch.num_rows
        if total == 0 and self._writer is None:
            return
        while offset < total:
            if self._writer is None:
                self._open(batch.schema)
            take = total - offset
            if self.roll_rows is not None:
                take = min(take, self.roll_rows - self._file_rows)
            piece = batch if (offset == 0 and take == total) else batch.slice(offset, take)
            self._encoder._write(self._writer, piece)
            self._file_rows += take
            self.rows += take
            offset += take
            if self._due():
                self._complete()

    def _due(self) -> bool:
        if self.roll_rows is not None and self._file_rows >= self.roll_rows:
            return True
        return (
            self.roll_seconds is not None and self._clock() - self._opened_at >= self.roll_seconds
        )

    def flush(self) -> None:
        """Complete the current file (when it holds rows), so that everything written so far is
        visible to readers."""
        if self._writer is not None and self._file_rows > 0:
            self._complete()

    def write_all(self, batches: Iterable[pa.RecordBatch]) -> None:
        for batch in batches:
            self.write_batch(batch)

    def close(self, *, schema: pa.Schema | None = None) -> int:
        """Complete the last file and return the rows written. When nothing was written and a
        ``schema`` is given, one empty file is still produced (a valid file of the table)."""
        try:
            if self._writer is None and not self.files and schema is not None:
                self._open(schema)
            self._complete()
            self._drop_stale_parts()
            if self._manifest:
                for folder in self._folders:
                    self._store.mark_success(folder)
        except BaseException:
            self.abort()
            raise
        return self.rows

    def _drop_stale_parts(self) -> None:
        """``overwrite`` replaces the table: after a run that completed, the parts of an earlier,
        larger run (same name pattern, in the folder of this run's files) are removed. Anything
        that does not follow the template stays."""
        if self._mode != "overwrite" or "{part}" not in self._template or not self._rels:
            return
        assert self._first_now is not None
        folder, pattern = self._part_pattern(self._first_now)
        for name in self._store.names(folder):
            rel = f"{folder}/{name}" if folder else name
            if pattern.fullmatch(name) and rel not in self._rels:
                self._store.delete(rel)

    def abort(self) -> None:
        """Drop the file in progress; files already published stay."""
        pending, self._pending, self._writer = self._pending, None, None
        if pending is not None:
            pending.abort()
