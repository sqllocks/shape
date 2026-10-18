"""Deterministic failure/replay simulation."""

from __future__ import annotations


def replay_with_failures(events, handler, checkpoint_store, fail_after=None):
    """Resume event handling from a checkpoint with optional injected failure."""
    cp = checkpoint_store.load()
    start = 0 if cp is None else cp.sequence
    processed = 0
    from .core import StreamCheckpoint

    for idx, event in enumerate(events):
        if idx < start:
            continue
        if fail_after is not None and processed == fail_after:
            raise RuntimeError("injected failure")
        handler(event)
        processed += 1
        checkpoint_store.save(StreamCheckpoint(idx + 1, str(idx + 1)))
    return processed
