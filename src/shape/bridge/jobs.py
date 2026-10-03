"""Bridge jobs: long-running commands that outlive the request, and the bridge process (P6-11).

A job is one command run in the background. Its state is one JSON file per job under the jobs
directory (``--jobs-dir``, else ``$SHAPE_JOBS_DIR``, else ``~/.shape/jobs``), in ``bridge/``::

    {"format": "shape-bridge-job", "version": 1, "job_id": ..., "command": ..., "status": ...,
     "request": {"args": {...secrets masked...}, "options": {...}}, "progress": {...},
     "result": ..., "error": ..., "worker": {"pid": ...}, "external": {...}, ...}

so a new bridge process reads the jobs an old one wrote. A job that was running when its process
died is reported as ``interrupted`` (the record says so; nothing claims it still runs). A file
written by a newer Shape (``version`` above this one) is refused with a message that says so.

Statuses: ``running``, ``succeeded`` (final), ``failed`` (final), ``cancelled`` (final),
``interrupted`` (final), and ``submitted`` for a remote run whose state lives elsewhere
(``external``).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shape.bridge.errors import to_bridge_error
from shape.bridge.protocol import BridgeError

if TYPE_CHECKING:
    from shape.bridge.context import Context

JOB_FORMAT = "shape-bridge-job"
JOB_VERSION = 1
JOBS_DIR_ENV = "SHAPE_JOBS_DIR"
ACTIVE = ("running", "submitted")
FINAL = ("succeeded", "failed", "cancelled", "interrupted")
_ID = re.compile(r"^job-[0-9a-f]{12}$")
_SECRET_KEY = re.compile(
    r"token|secret|passw|credential|connection_?string|api_?key|access_?key|sas", re.IGNORECASE
)
MASK = "***"


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def default_jobs_dir() -> Path:
    return Path(os.environ.get(JOBS_DIR_ENV) or Path.home() / ".shape" / "jobs")


def mask_secrets(value: Any) -> Any:
    """``value`` with every secret-named key's value replaced by ``***`` (a job file never holds a
    credential)."""
    if isinstance(value, dict):
        return {
            k: (MASK if _SECRET_KEY.search(str(k)) and v not in (None, "") else mask_secrets(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [mask_secrets(v) for v in value]
    return value


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class JobStore:
    """The job files of one directory."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.dir = self.root / "bridge"
        self._lock = threading.RLock()

    def path(self, job_id: str) -> Path:
        if not _ID.match(job_id):
            raise BridgeError(
                "input.unknown_job", f"no job {job_id!r}", "job ids look like job-0123456789ab"
            )
        return self.dir / f"{job_id}.json"

    def write(self, record: dict[str, Any]) -> None:
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            record["updated_at"] = now_iso()
            target = self.path(record["job_id"])
            fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".job-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(record, handle, indent=2, sort_keys=True, default=str)
                os.chmod(tmp, 0o600)
                os.replace(tmp, target)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise

    def read(self, job_id: str) -> dict[str, Any]:
        path = self.path(job_id)
        with self._lock:
            try:
                text = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                raise BridgeError(
                    "input.unknown_job", f"no job {job_id!r}", "run `job_list`"
                ) from None
        try:
            record = json.loads(text)
        except ValueError as exc:
            raise BridgeError(
                "input.invalid_schema", f"the job file for {job_id} is not valid JSON: {exc}"
            ) from exc
        return check_fields(check_record(record, job_id), job_id)

    def ids(self) -> list[str]:
        if not self.dir.is_dir():
            return []
        return sorted(p.stem for p in self.dir.glob("job-*.json") if _ID.match(p.stem))


def check_record(record: Any, job_id: str) -> dict[str, Any]:
    """A job record read from disk, or the error that says why it cannot be used."""
    if not isinstance(record, dict) or record.get("format") != JOB_FORMAT:
        raise BridgeError(
            "input.invalid_schema", f"the job file for {job_id} is not a bridge job record"
        )
    version = record.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise BridgeError(
            "input.invalid_schema", f"the job file for {job_id} has no valid version number"
        )
    if version > JOB_VERSION:
        raise BridgeError(
            "input.unsupported_format_version",
            f"the job file for {job_id} is version {version}, written by a newer Shape; this "
            f"Shape reads versions up to {JOB_VERSION}",
            "upgrade Shape to read it",
        )
    return record


#: The fields a job record must hold, with the types they may have.
_FIELDS: dict[str, tuple[type, ...]] = {
    "job_id": (str,),
    "command": (str,),
    "status": (str,),
    "created_at": (str,),
    "progress": (dict, type(None)),
    "worker": (dict, type(None)),
    "external": (dict, type(None)),
}


def check_fields(record: dict[str, Any], job_id: str) -> dict[str, Any]:
    """A record whose fields have the types the bridge reads them as, and which is the job its
    file is named for (a copied or renamed file is not another job)."""
    for name, types in _FIELDS.items():
        if not isinstance(record.get(name), types):
            raise BridgeError(
                "input.invalid_schema", f"the job file for {job_id} has no valid {name!r}"
            )
    if record["job_id"] != job_id:
        raise BridgeError(
            "input.invalid_schema",
            f"the job file for {job_id} holds the record of {record['job_id']}",
        )
    return record


class JobCancelled(Exception):
    """Raised inside a job by the code that noticed the cancel request. ``result`` is what the job
    had done by then, kept in the record."""

    def __init__(self, result: dict[str, Any] | None = None) -> None:
        super().__init__("cancelled")
        self.result = result


class Jobs:
    """Starts, tracks and cancels bridge jobs (the threads live in this process)."""

    def __init__(self, store: JobStore) -> None:
        self.store = store
        self._live: dict[str, tuple[threading.Thread, threading.Event]] = {}
        self._lock = threading.Lock()

    # -- creating ---------------------------------------------------------------------------

    def _new(
        self,
        command: str,
        args: dict[str, Any],
        options: dict[str, Any],
        status: str,
        external: dict[str, Any] | None,
    ) -> dict[str, Any]:
        record: dict[str, Any] = {
            "format": JOB_FORMAT,
            "version": JOB_VERSION,
            "job_id": f"job-{uuid.uuid4().hex[:12]}",
            "command": command,
            "status": status,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "request": {"args": mask_secrets(args), "options": dict(options)},
            "progress": {},
            "result": None,
            "error": None,
            "worker": {"pid": os.getpid()},
            "external": external,
            "cancellable": False,
        }
        self.store.write(record)
        return record

    def start(
        self,
        command: str,
        args: dict[str, Any],
        options: dict[str, Any],
        run: Callable[[Context], dict[str, Any]],
        make_context: Callable[[threading.Event, Callable[[dict[str, Any]], None]], Context],
        *,
        cancellable: bool = False,
    ) -> dict[str, Any]:
        """Run ``run(context)`` on a thread as a new job; returns the job at once."""
        record = self._new(command, args, options, "running", None)
        record["cancellable"] = cancellable
        self.store.write(record)
        job_id = record["job_id"]
        cancel = threading.Event()
        last = [0.0]

        def progress(info: dict[str, Any]) -> None:
            now = time.monotonic()
            if now - last[0] < 0.25:  # one file write per chunk would slow a fast run
                return
            last[0] = now
            self.update(job_id, progress=info)

        context = make_context(cancel, progress)

        def work() -> None:
            try:
                result = run(context)
            except JobCancelled as stopped:
                self.update(job_id, status="cancelled", result=stopped.result)
            except BaseException as exc:  # a job never takes the process down
                if cancel.is_set() and type(exc).__name__ == "ScaleCancelled":
                    self.update(job_id, status="cancelled")
                else:
                    self.update(job_id, status="failed", error=to_bridge_error(exc).to_dict())
            else:
                self.update(job_id, status="succeeded", result=result)

        thread = threading.Thread(target=work, name=f"shape-bridge-{job_id}", daemon=True)
        with self._lock:
            # A finished thread has recorded its job's end: let it go (a long session would
            # otherwise keep every job's thread).
            self._live = {k: v for k, v in self._live.items() if v[0].is_alive()}
            self._live[job_id] = (thread, cancel)
        thread.start()
        return self.get(job_id)

    def register_external(
        self,
        command: str,
        args: dict[str, Any],
        options: dict[str, Any],
        external: dict[str, Any],
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """A job whose run lives elsewhere (a Fabric notebook run): recorded as ``submitted``."""
        record = self._new(command, args, options, "submitted", external)
        if result is not None:
            record["result"] = result
            self.store.write(record)
        return record

    # -- reading and changing -----------------------------------------------------------------

    def update(self, job_id: str, **changes: Any) -> dict[str, Any]:
        with self.store._lock:
            record = self.store.read(job_id)
            if record["status"] in FINAL:
                return record  # a final job does not change again
            record.update(changes)
            self.store.write(record)
            return record

    def get(self, job_id: str) -> dict[str, Any]:
        record = self.store.read(job_id)
        return self._settle(record)

    def _settle(self, record: dict[str, Any]) -> dict[str, Any]:
        """A job recorded as running with no live worker is ``interrupted``. A job that runs
        elsewhere (``external``) has no worker here: its state is asked of the service."""
        if record["status"] != "running" or record.get("external"):
            return record
        job_id = record["job_id"]
        with self._lock:
            live = self._live.get(job_id)
        if live is not None and live[0].is_alive():
            return record
        pid = int((record.get("worker") or {}).get("pid") or 0)
        if pid and pid != os.getpid() and pid_alive(pid):
            return record  # another bridge process runs it
        return self.update(
            job_id,
            status="interrupted",
            error=BridgeError(
                "input.job_interrupted",
                "the process that ran this job ended before the job finished",
                "run the command again",
            ).to_dict(),
        )

    def list(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for job_id in self.store.ids():
            try:
                out.append(self.get(job_id))
            except BridgeError:
                continue  # an unreadable file is not a job to list
        return sorted(out, key=lambda r: (r["created_at"], r["job_id"]))

    def cancel(self, job_id: str, timeout: float = 30.0) -> tuple[dict[str, Any], bool]:
        """Cancel a job. Returns ``(job, cancelled)``: ``cancelled`` is False for a job that was
        already final, or that finished before it saw the request."""
        record = self.get(job_id)
        if record["status"] in FINAL:
            return record, False
        with self._lock:
            live = self._live.get(job_id)
        if live is not None and not record.get("cancellable"):
            raise BridgeError(
                "input.job_state",
                f"{job_id} ({record['command']}) cannot be cancelled while it runs",
                "wait for it to finish",
            )
        if live is None:
            pid = int((record.get("worker") or {}).get("pid") or 0)
            if record["status"] == "running" and pid and pid != os.getpid():
                raise BridgeError(
                    "input.job_state",
                    f"{job_id} runs in another bridge process (pid {pid}): cancel it there",
                )
            return self.update(job_id, status="cancelled"), True
        live[1].set()
        live[0].join(timeout=timeout)
        final = self.get(job_id)
        return final, final["status"] == "cancelled"


def describe(record: dict[str, Any]) -> dict[str, Any]:
    """A job as a client sees it (never the request: that stays in the file)."""
    return {
        "job_id": record["job_id"],
        "command": record["command"],
        "status": record["status"],
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
        "progress": record.get("progress") or {},
        "result": record.get("result"),
        "error": record.get("error"),
        "cancellable": bool(record.get("cancellable")),
    }


def drain(jobs: Jobs, timeout: float) -> None:
    """End of input: ask the jobs that notice a cancel request to stop, then wait for every job
    thread of this process (up to ``timeout`` seconds in all), so the files say what happened.
    A job still running after that is left ``running`` and reads as ``interrupted`` next time."""
    with jobs._lock:
        live = list(jobs._live.values())
    for _thread, cancel in live:
        cancel.set()  # a job that cannot notice it simply runs on
    deadline = time.monotonic() + timeout
    for thread, _cancel in live:
        thread.join(max(0.0, deadline - time.monotonic()))
