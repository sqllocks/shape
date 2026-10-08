"""Synchronous and asynchronous bounded streaming primitives with checkpoint hooks."""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator, Callable, Iterable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class StreamCheckpoint:
    sequence: int
    token: str | None = None


def consume(iterable: Iterable[T], fn: Callable[[T], None]):
    count = 0
    for item in iterable:
        fn(item)
        count += 1
    return count


async def aconsume(
    source: AsyncIterable[T],
    fn: Callable[[T], None],
    checkpoint_every: int = 1000,
    checkpoint: Callable[[StreamCheckpoint], None] | None = None,
) -> int:
    count = 0
    async for item in source:
        fn(item)
        count += 1
        if checkpoint and checkpoint_every and count % checkpoint_every == 0:
            checkpoint(StreamCheckpoint(count))
    if checkpoint:
        checkpoint(StreamCheckpoint(count))
    return count


async def bounded_map(source: AsyncIterable[T], fn: Callable[[T], T]) -> AsyncIterator[T]:
    # Pull-based async iteration naturally applies backpressure: no next item is requested until
    # yield resumes.
    async for item in source:
        yield fn(item)
