"""A progress writer cannot overwrite another store's durable cancellation."""

from __future__ import annotations

import threading

from shape.demo import filelock
from shape.scale.jobs import JobRecord, Jobs, JobStore


def test_cross_store_progress_preserves_cancel_during_write(tmp_path, monkeypatch):
    writer = JobStore(tmp_path)
    canceller = JobStore(tmp_path)
    writer.put(JobRecord("local-race", "local", status="running"))
    writing = threading.Event()
    release = threading.Event()
    errors = []
    write_progress = writer._write
    write_cancel = canceller._write
    try_lock = filelock._try_lock

    def paused_progress(record):
        writing.set()
        assert release.wait(5), "cancel neither completed nor contended for the lock"
        write_progress(record)

    def cancelling(record):
        write_cancel(record)
        # Without serialization this happens before the stale progress write.
        release.set()

    def observed_lock(fd):
        try:
            try_lock(fd)
        except OSError:
            # With serialization the cancel writer waits for the progress writer.
            release.set()
            raise

    monkeypatch.setattr(writer, "_write", paused_progress)
    monkeypatch.setattr(canceller, "_write", cancelling)
    monkeypatch.setattr(filelock, "_try_lock", observed_lock)

    def progress():
        try:
            writer.update("local-race", progress={"rows_done": 1})
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=progress)
    worker.start()
    try:
        assert writing.wait(5)
        assert Jobs(canceller).cancel("local-race")["cancelled"] is True
    finally:
        release.set()
        worker.join(timeout=5)
    assert not worker.is_alive()
    assert errors == []
    record = JobStore(tmp_path).get("local-race")
    assert record.status == "cancelled"
    assert record.progress == {"rows_done": 1}
