"""The sink contract: where the router puts generated chunks.

A sink receives the rows of each table as Arrow record batches, in row order, between one
``open`` and one ``close``. ``finish_table`` says a table has no more batches, so a sink that holds
a table until it is whole (a database load) writes it then, one table at a time, instead of
holding every table until the end. The router calls a sink from one thread at a time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    import pyarrow as pa  # type: ignore[import-untyped]

    from shape.generation.schema import GenSchema


@runtime_checkable
class Sink(Protocol):
    """What a sink must implement. ``finish_table`` is optional (see :class:`BaseSink`)."""

    def open(self, schema: GenSchema | None) -> None:
        """Called once before any batch: create directories, tables or connections."""
        ...

    def write_batch(self, table: str, batch: pa.RecordBatch) -> None:
        """One batch of ``table``, after the batches already written for it."""
        ...

    def close(self) -> None:
        """Called once after the last batch: flush, commit, release."""
        ...


class BaseSink:
    """The four hooks, each doing nothing; a sink overrides what it needs."""

    name = "sink"

    def open(self, schema: GenSchema | None) -> None:
        return None

    def write_batch(self, table: str, batch: pa.RecordBatch) -> None:
        return None

    def finish_table(self, table: str) -> None:
        return None

    def close(self) -> None:
        return None


class SinkError(Exception):
    """One or more sinks failed in a call; ``sink_errors`` pairs each sink's name with its error."""

    def __init__(self, errors: list[tuple[str, Exception]]) -> None:
        self.sink_errors = errors
        from shape.security.redact import redact_text

        super().__init__(
            redact_text("sink failures: " + "; ".join(f"{name}: {exc}" for name, exc in errors))
        )


def sink_name(sink: object) -> str:
    """A sink's name for messages: its ``name`` attribute, else its class name."""
    name = getattr(sink, "name", None)
    return name if isinstance(name, str) and name else type(sink).__name__


@dataclass(frozen=True)
class FabricConnectionProfile:
    """The sign-in a Fabric-backed sink shares: a bearer ``token`` and the ``endpoint`` it is for.
    The token is left out of ``repr`` so it does not reach a log line."""

    token: str = field(repr=False)
    endpoint: str
