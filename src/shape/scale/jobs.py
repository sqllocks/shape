"""Jobs: the durable job store, the Fabric job tracker and the stream manager (P6-13).

A *job* is one submitted run: a ``fabric_spark`` notebook run, or a local run that was given a job
record. The store keeps a JSON file per job under a directory (``$SHAPE_JOBS_DIR``, default
``~/.shape/jobs``) so a job outlives the process that submitted it: its status can be asked, it can
be cancelled, and it can be resumed from another process. A record never holds a credential; a
call that talks to Fabric takes the token when it is made (``SHAPE_FABRIC_TOKEN`` for the command
line).

States: ``submitted``, ``running``, ``succeeded`` (final), ``failed`` (final, resumable),
``cancelled`` (final, resumable).
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shape import compat
from shape.scale.http import FABRIC_API, Http, Transport

logger = logging.getLogger(__name__)

JOBS_DIR_ENV = "SHAPE_JOBS_DIR"
TOKEN_ENV = "SHAPE_FABRIC_TOKEN"  # noqa: S105  # nosec B105  # the variable's name, not a secret
MASK = "***"


def _safe(text: str) -> str:
    """``text`` (an error that may echo a connection string) with secrets hidden."""
    from shape.security.redact import redact_text

    return redact_text(text)


ACTIVE = ("submitted", "running")
FINAL = ("succeeded", "failed", "cancelled")
RESUMABLE = ("failed", "cancelled")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")

# Fabric's job status names, as Shape reports them.
STATUS_MAP: dict[str, str] = {
    "NotStarted": "submitted",
    "InProgress": "running",
    "Deduplicating": "running",
    "Completed": "succeeded",
    "Failed": "failed",
    "Cancelled": "cancelled",
    # A run that was not started because an identical one was running: nothing of its own to wait
    # for, so it is final, and ``cancelled`` (the closest) lets it be resumed.
    "Deduped": "cancelled",
}


class JobNotFoundError(KeyError):
    """No job has this id."""


class JobStateError(RuntimeError):
    """The job is not in a state the call needs (resuming a running job, say)."""


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def default_jobs_dir() -> Path:
    """Return the default local directory for persisted job state."""
    return Path(os.environ.get(JOBS_DIR_ENV) or Path.home() / ".shape" / "jobs")


def windows_current_user() -> str:
    """``DOMAIN\\user`` of the account running this process (the name icacls accepts)."""
    import getpass

    user = os.environ.get("USERNAME") or getpass.getuser()
    domain = os.environ.get("USERDOMAIN")
    return f"{domain}\\{user}" if domain else user


def restrict_to_current_user(path: Path) -> None:
    """Make ``path`` (a directory or a file) readable and writable by the current user only.

    POSIX needs nothing here: directories are created with mode 0700 and files with 0600.
    Windows ignores POSIX modes, so the ACL is rewritten with ``icacls``, which ships with Windows:
    inheritance from the parent is removed and the current user is the only entry. A directory
    also gets object and container inheritance, but the store sets the ACL of each file itself, so
    the result does not depend on what the parent (a profile, a temp folder, or the ACL Python 3.13
    gives ``mkdir(mode=0o700)``) hands down. Raises ``OSError`` when the ACL cannot be set: a store
    that cannot be made private is not used.
    """
    if sys.platform != "win32":
        return
    rights = "(OI)(CI)F" if path.is_dir() else "F"
    try:
        done = subprocess.run(
            [
                "icacls",
                str(path),
                "/inheritance:r",
                "/grant:r",
                f"{windows_current_user()}:{rights}",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError(f"cannot restrict {path} to the current user: {exc}") from exc
    if done.returncode != 0:
        raise OSError(
            f"cannot restrict {path} to the current user: icacls exited "
            f"{done.returncode}: {(done.stderr or done.stdout).strip()}"
        )


# The job record is a persisted file: it declares ``format`` and ``version`` like the kinds of
# ``shape.compat.KINDS`` (this kind is local to the job store).
JOB_KIND = compat.Kind(
    name="job-record",
    label="job record",
    format="shape-job",
    current=1,
    first_release={1: "0.9.0"},
    implicit_version=1,
)


@dataclass
class JobRecord:
    """One job. ``request`` is what was asked (never a secret); ``fabric`` holds the ids of a
    ``fabric_spark`` run."""

    job_id: str
    kind: str
    status: str = "submitted"
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    request: dict[str, Any] = field(default_factory=dict)
    progress: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None
    fabric: dict[str, Any] = field(default_factory=dict)
    attempts: int = 1
    # fields a newer release wrote: kept, so an update by this release does not drop them
    extra: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        doc = asdict(self)
        extra = doc.pop("extra")
        return compat.stamp(JOB_KIND, {**extra, **doc}, aliases=False)

    @classmethod
    def from_dict(cls, doc: dict[str, Any], source: object = "") -> JobRecord:
        compat.check_format(JOB_KIND, doc)
        compat.check_readable(JOB_KIND, doc, source)
        known = {f.name for f in fields(cls)} - {"extra"}
        compat.check_unknown(JOB_KIND, doc, known)
        _, extra = compat.split_extras(JOB_KIND, doc, known)
        return cls(**{k: v for k, v in doc.items() if k in known}, extra=extra)


def new_job_id(kind: str) -> str:
    """Return a new unique identifier for a persisted job."""
    prefix = "spark" if kind == "fabric_spark" else "local"
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _read_record(path: Path, job_id: str) -> JobRecord:
    """The record in ``path``; a ``ValueError`` naming the file when it is not a job record of
    ``job_id`` (damaged, or written by something else)."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"job file {path.name} is not valid JSON: {exc}") from None
    if (
        not isinstance(doc, dict)
        or doc.get("job_id") != job_id
        or not isinstance(doc.get("kind"), str)
    ):
        raise ValueError(f"job file {path.name} is not a job record of {job_id}")
    try:
        return JobRecord.from_dict(doc, path.name)
    except TypeError as exc:
        raise ValueError(f"job file {path.name} is not a job record: {exc}") from None


class JobStore:
    """Thread-safe store of :class:`JobRecord`; durable when it has a directory."""

    def __init__(self, root: str | Path | None = None) -> None:
        self._root = Path(root) if root is not None else None
        self._lock = threading.RLock()
        self._jobs: dict[str, JobRecord] = {}
        self._secured = False

    @classmethod
    def default(cls) -> JobStore:
        return cls(default_jobs_dir())

    @property
    def root(self) -> Path | None:
        return self._root

    def _path(self, job_id: str) -> Path:
        if self._root is None:
            raise AssertionError("memory store has no path")
        if not _ID.fullmatch(job_id):
            raise JobNotFoundError(job_id)
        return self._root / f"{job_id}.json"

    def _write(self, record: JobRecord) -> None:
        if self._root is None:
            return
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self._secured:
            restrict_to_current_user(self._root)
            self._secured = True
        target = self._path(record.job_id)
        fd, tmp = tempfile.mkstemp(dir=self._root, prefix=".job-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(record.to_dict(), handle, indent=2, sort_keys=True, default=str)
            os.chmod(tmp, 0o600)
            restrict_to_current_user(Path(tmp))
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def put(self, record: JobRecord) -> JobRecord:
        with self._lock:
            if not _ID.fullmatch(record.job_id):
                raise ValueError(f"invalid job id {record.job_id!r}")
            record.updated_at = now_iso()
            self._jobs[record.job_id] = record
            self._write(record)
        return record

    def get(self, job_id: str) -> JobRecord:
        """The job's record; reads the file when this store has not seen it (another process)."""
        with self._lock:
            if self._root is not None and _ID.fullmatch(job_id):
                path = self._path(job_id)
                if path.is_file():
                    cached = self._jobs.get(job_id)
                    disk = _read_record(path, job_id)
                    if cached is None or disk.updated_at >= cached.updated_at:
                        self._jobs[job_id] = disk
            record = self._jobs.get(job_id)
            if record is None:
                raise JobNotFoundError(job_id)
            return record

    def update(self, job_id: str, **changes: Any) -> JobRecord:
        with self._lock:
            record = self.get(job_id)
            for key, value in changes.items():
                if not hasattr(record, key):
                    raise AttributeError(f"a job has no field {key!r}")
                setattr(record, key, value)
            return self.put(record)

    def list(self) -> list[JobRecord]:
        with self._lock:
            if self._root is not None and self._root.is_dir():
                for path in sorted(self._root.glob("*.json")):
                    try:
                        self.get(path.stem)
                    except JobNotFoundError:
                        continue
                    except ValueError as exc:  # JSONDecodeError included
                        logger.warning("skipping job file %s: %s", path.name, exc)
                        continue
            return sorted(self._jobs.values(), key=lambda r: (r.created_at, r.job_id))

    def delete(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)
            if self._root is not None and _ID.fullmatch(job_id):
                self._path(job_id).unlink(missing_ok=True)


class FabricJobTracker:
    """Polls and cancels Fabric notebook runs through the Jobs REST API."""

    def __init__(self, token: str, transport: Transport | None = None) -> None:
        self._http = Http(token, transport)

    @staticmethod
    def _url(workspace_id: str, item_id: str, run_id: str) -> str:
        return f"{FABRIC_API}/workspaces/{workspace_id}/items/{item_id}/jobs/instances/{run_id}"

    def get_status(self, workspace_id: str, item_id: str, run_id: str) -> dict[str, Any]:
        """``status`` (Shape's name), ``fabric_status`` (Fabric's), ``fabric_run_id`` and, when
        the run failed, ``error``."""
        data = self._http.request("GET", self._url(workspace_id, item_id, run_id)).json_object()
        raw = str(data.get("status", "Unknown"))
        out: dict[str, Any] = {
            "status": STATUS_MAP.get(raw, raw.lower()),
            "fabric_status": raw,
            "fabric_run_id": run_id,
        }
        reason = data.get("failureReason")
        if reason:
            out["error"] = str(
                reason.get("message", reason) if isinstance(reason, dict) else reason
            )
        return out

    def cancel(self, workspace_id: str, item_id: str, run_id: str) -> dict[str, Any]:
        self._http.request("POST", self._url(workspace_id, item_id, run_id) + "/cancel")
        return {"cancelled": True, "fabric_run_id": run_id}


# ---- the jobs a caller drives ------------------------------------------------------------------


class Jobs:
    """Submit, status, cancel and resume, over a :class:`JobStore`.

    ``fabric_spark`` jobs are driven through Fabric (``token`` per call); ``local`` jobs through a
    runner (:class:`LocalRunner`) that this object starts in a background thread.
    """

    def __init__(
        self,
        store: JobStore | None = None,
        *,
        transport: Transport | None = None,
    ) -> None:
        self.store = store or JobStore()
        self._transport = transport
        self._local: dict[str, tuple[threading.Thread, threading.Event]] = {}
        self._lock = threading.Lock()

    # -- fabric_spark ---------------------------------------------------------------------

    def register_spark(self, record: JobRecord) -> JobRecord:
        return self.store.put(record)

    def status(self, job_id: str, token: str | None = None) -> dict[str, Any]:
        record = self.store.get(job_id)
        if record.kind == "fabric_spark" and record.status in ACTIVE:
            tracker = FabricJobTracker(_need_token(token), self._transport)
            polled = tracker.get_status(
                record.fabric["workspace_id"],
                record.fabric["notebook_item_id"],
                record.fabric["fabric_run_id"],
            )
            changes: dict[str, Any] = {"status": polled["status"]}
            if polled.get("error"):
                changes["error"] = _safe(polled["error"])
            record = self.store.update(job_id, **changes)
        return self.describe(record)

    def cancel(self, job_id: str, token: str | None = None) -> dict[str, Any]:
        record = self.store.get(job_id)
        if record.status in FINAL:
            return {**self.describe(record), "cancelled": False}
        if record.kind == "fabric_spark":
            tracker = FabricJobTracker(_need_token(token), self._transport)
            tracker.cancel(
                record.fabric["workspace_id"],
                record.fabric["notebook_item_id"],
                record.fabric["fabric_run_id"],
            )
            record = self.store.update(job_id, status="cancelled")
            return {**self.describe(record), "cancelled": True}
        with self._lock:
            running = self._local.get(job_id)
        if running is None:
            # Recorded as active, but nothing in this process runs it (the process that did died).
            record = self.store.update(
                job_id, status="cancelled", error="no live run; marked cancelled"
            )
            return {**self.describe(record), "cancelled": True}
        running[1].set()
        running[0].join(timeout=30)
        final = self.store.get(job_id)
        # A run that finished before it saw the request is a success, not a cancellation.
        return {**self.describe(final), "cancelled": final.status == "cancelled"}

    @staticmethod
    def describe(record: JobRecord) -> dict[str, Any]:
        return {
            "job_id": record.job_id,
            "kind": record.kind,
            "status": record.status,
            "progress": record.progress,
            "result": record.result,
            "error": record.error,
            "attempts": record.attempts,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "resumable": record.status in RESUMABLE,
            **({"fabric": {k: v for k, v in record.fabric.items()}} if record.fabric else {}),
        }

    # -- local runs -----------------------------------------------------------------------

    def start_local(
        self,
        request: dict[str, Any],
        run: Callable[
            [dict[str, Any], threading.Event, Callable[[dict[str, Any]], None], bool],
            dict[str, Any],
        ],
        *,
        stored: dict[str, Any] | None = None,
        job_id: str | None = None,
        resume: bool = False,
        wait: bool = False,
        overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run ``run(request, cancel, progress, resume)`` as a job in a background thread (or
        inline with ``wait=True``). A new job, or ``job_id`` of a resumable one.

        The record holds ``stored`` (``request`` with its secrets masked) when that is given; the
        run itself gets ``request``. A resumed run gets the record's request with ``overrides`` on
        top: the settings a stored request could not keep (a masked secret) come again there."""
        live = request
        if job_id is None:
            record = self.store.put(
                JobRecord(
                    new_job_id("local"), "local", request=stored if stored is not None else request
                )
            )
        else:
            record = self.store.get(job_id)
            if record.kind != "local":
                raise JobStateError(f"{job_id} is not a local job")
            if record.status not in RESUMABLE and not (
                record.status in ACTIVE and job_id not in self._local
            ):
                raise JobStateError(
                    f"{job_id} is {record.status}: only a failed or cancelled job resumes"
                )
            live = _merged(record.request, overrides)
            record = self.store.update(
                job_id, status="submitted", error=None, attempts=record.attempts + 1
            )
        runner = LocalRunner(
            self.store, record.job_id, run, live, resume=resume or job_id is not None
        )
        cancel = runner.cancel
        thread = threading.Thread(target=runner.run, name=f"shape-job-{record.job_id}", daemon=True)
        with self._lock:
            self._local[record.job_id] = (thread, cancel)
        thread.start()
        if wait:
            thread.join()
            with self._lock:
                self._local.pop(record.job_id, None)
            return self.describe(self.store.get(record.job_id))
        return self.describe(self.store.get(record.job_id))

    def wait(self, job_id: str, timeout: float | None = None) -> dict[str, Any]:
        with self._lock:
            running = self._local.get(job_id)
        if running is not None:
            running[0].join(timeout)
            if not running[0].is_alive():
                with self._lock:
                    self._local.pop(job_id, None)
        return self.describe(self.store.get(job_id))

    # -- resume ---------------------------------------------------------------------------

    def resume_spark(
        self,
        job_id: str,
        token: str,
        submit: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        """Resume a ``fabric_spark`` job. One still active at Fabric is re-attached (its status
        polled, nothing submitted again). A failed or cancelled one is submitted again with
        ``submit(request)``, which returns the new Fabric ids, under the same job id."""
        record = self.store.get(job_id)
        if record.kind != "fabric_spark":
            raise JobStateError(f"{job_id} is not a fabric_spark job")
        if record.status in ACTIVE:
            return self.status(job_id, token)
        if record.status == "succeeded":
            raise JobStateError(f"{job_id} already succeeded; nothing to resume")
        fabric = submit(record.request)
        record = self.store.update(
            job_id,
            status="submitted",
            error=None,
            fabric={**record.fabric, **fabric},
            attempts=record.attempts + 1,
        )
        return self.describe(record)


def _merged(base: dict[str, Any], overrides: dict[str, Any] | None) -> dict[str, Any]:
    """``base`` with ``overrides`` on top; a dict value is merged one level down (so the settings
    of one sink can be given again without the others)."""
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for key, value in (overrides or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            for inner_key, inner in value.items():
                if isinstance(inner, dict) and isinstance(out[key].get(inner_key), dict):
                    out[key][inner_key] = {**out[key][inner_key], **inner}
                else:
                    out[key][inner_key] = inner
        else:
            out[key] = value
    masked = [
        s for s, cfg in out.get("sink_config", {}).items() for v in cfg.values() if _masked(v)
    ]
    if masked:
        raise JobStateError(
            f"the stored request holds masked settings for sink(s) {sorted(set(masked))}: "
            "give them again to resume (--sink-config)"
        )
    auth = out.get("auth")
    if isinstance(auth, dict) and any(_masked(v) for v in auth.values()):
        raise JobStateError(
            "the stored request holds masked sign-in settings (auth): give them again to resume"
        )
    return out


# A mask inside a string: ``Pwd=***;`` in a connection string, ``user:***@host`` in a URL.
_EMBEDDED_MASK = re.compile(r"[=:]\s*" + re.escape(MASK) + r"(?=[;&@\s'\"}]|$)")


def _masked(value: Any) -> bool:
    """True for a setting that is the mask, or a string that holds it in place of a secret."""
    return value == MASK or (isinstance(value, str) and _EMBEDDED_MASK.search(value) is not None)


def _need_token(token: str | None) -> str:
    value = token or os.environ.get(TOKEN_ENV, "")
    if not value:
        raise ValueError(f"a Fabric token is needed: pass one, or set {TOKEN_ENV}")
    return value


class LocalRunner:
    """Runs one local job and records its progress, result and end state in the store."""

    def __init__(
        self,
        store: JobStore,
        job_id: str,
        run: Callable[
            [dict[str, Any], threading.Event, Callable[[dict[str, Any]], None], bool],
            dict[str, Any],
        ],
        request: dict[str, Any],
        *,
        resume: bool,
    ) -> None:
        self.store = store
        self.job_id = job_id
        self._run = run
        self._request = request
        self._resume = resume
        self.cancel = threading.Event()
        self._last = 0.0

    def _progress(self, info: dict[str, Any]) -> None:
        now = time.monotonic()
        if now - self._last < 0.25:  # a file write per chunk would slow a fast run
            return
        self._last = now
        # `shape jobs cancel` in another process can only write the record: read it here, so
        # the run stops before its next chunk (the router checks ``cancel`` between chunks).
        try:
            stored = self.store.get(self.job_id).status
        except (JobNotFoundError, OSError, ValueError):
            stored = None  # a record that cannot be read does not stop the run
        if stored == "cancelled":
            self.cancel.set()
            return
        self.store.update(self.job_id, progress=info)

    def run(self) -> None:
        from shape.scale.router import ScaleCancelled

        self.store.update(self.job_id, status="running")
        try:
            result = self._run(self._request, self.cancel, self._progress, self._resume)
        except ScaleCancelled:
            self.store.update(self.job_id, status="cancelled")
        except BaseException as exc:
            logger.error("job %s failed: %s", self.job_id, _safe(str(exc)))
            self.store.update(
                self.job_id, status="failed", error=_safe(f"{type(exc).__name__}: {exc}")
            )
        else:
            self.store.update(
                self.job_id,
                status="succeeded",
                result=result,
                progress={
                    "rows_done": result.get("rows_generated", 0),
                    "rows_total": result.get("rows_generated", 0),
                },
            )


# ---- streams -----------------------------------------------------------------------------------


@dataclass
class StreamState:
    """Persisted cursor and checkpoint state for a streaming job."""

    stream_id: str
    chunks_written: int = 0
    rows_written: int = 0
    running: bool = True
    error: str | None = None
    stop_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = field(default=None, repr=False)
    counter_lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, rows: int) -> None:
        """Count one written chunk of ``rows`` rows (the rows actually written)."""
        with self.counter_lock:
            self.chunks_written += 1
            self.rows_written += rows


class StreamManager:
    """Background streaming jobs, one instance per process."""

    _instance: StreamManager | None = None
    _class_lock = threading.Lock()

    @classmethod
    def instance(cls) -> StreamManager:
        with cls._class_lock:
            if cls._instance is None:
                cls._instance = StreamManager()
            return cls._instance

    def __init__(self) -> None:
        self._streams: dict[str, StreamState] = {}
        self._lock = threading.Lock()

    def start(self, run_fn: Callable[[StreamState], None]) -> str:
        """Run ``run_fn(state)`` in a daemon thread; returns the stream id."""
        stream_id = uuid.uuid4().hex
        state = StreamState(stream_id=stream_id)
        thread = threading.Thread(
            target=self._run,
            args=(run_fn, state),
            name=f"shape-stream-{stream_id[:8]}",
            daemon=True,
        )
        state.thread = thread
        with self._lock:
            self._streams[stream_id] = state
        thread.start()
        return stream_id

    @staticmethod
    def _run(run_fn: Callable[[StreamState], None], state: StreamState) -> None:
        try:
            run_fn(state)
        except Exception as exc:
            state.error = _safe(f"{type(exc).__name__}: {exc}")
            logger.error("stream %s failed: %s", state.stream_id, state.error)
        finally:
            state.running = False

    def status(self, stream_id: str) -> dict[str, Any]:
        with self._lock:
            state = self._streams.get(stream_id)
        if state is None:
            return {"error": f"unknown stream_id: {stream_id}"}
        with state.counter_lock:
            return {
                "stream_id": stream_id,
                "chunks_written": state.chunks_written,
                "rows_written": state.rows_written,
                "running": state.running,
                "error": state.error,
            }

    def stop(self, stream_id: str, timeout: float = 5.0) -> bool | None:
        """Ask a stream to stop and wait up to ``timeout`` seconds. ``None``: unknown id.
        ``True``: stopped. ``False``: still running after the timeout (it stays listed, so it can
        be stopped again)."""
        with self._lock:
            state = self._streams.get(stream_id)
        if state is None:
            return None
        state.stop_event.set()
        if state.thread is not None and state.thread.is_alive():
            state.thread.join(timeout=timeout)
        stopped = not (state.thread is not None and state.thread.is_alive())
        if stopped:
            with self._lock:
                self._streams.pop(stream_id, None)
        return stopped


__all__ = [
    "ACTIVE",
    "FINAL",
    "JOBS_DIR_ENV",
    "MASK",
    "RESUMABLE",
    "STATUS_MAP",
    "TOKEN_ENV",
    "FabricJobTracker",
    "JobNotFoundError",
    "JobRecord",
    "JobStateError",
    "JobStore",
    "Jobs",
    "LocalRunner",
    "StreamManager",
    "StreamState",
    "default_jobs_dir",
    "new_job_id",
]
