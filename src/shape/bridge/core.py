"""The bridge core: one request in, one response out, and it never raises (P6-11)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from shape.bridge.context import Context
from shape.bridge.errors import to_bridge_error
from shape.bridge.handlers.common import jsonable
from shape.bridge.jobs import Jobs, JobStore, default_jobs_dir, describe, drain
from shape.bridge.protocol import (
    BridgeError,
    Request,
    error_response,
    ok_response,
    parse_request,
)
from shape.bridge.registry import COMMANDS
from shape.bridge.spec import Command, check_args, check_options, minor_of


class Bridge:
    """Serves requests against one jobs directory.

    ``handle`` takes a request (its JSON text or the decoded object) and returns the response
    object. Whatever goes wrong becomes an error response; only ``KeyboardInterrupt`` and
    ``SystemExit`` pass through."""

    def __init__(self, jobs_dir: str | Path | None = None) -> None:
        root = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
        root = root.absolute()  # result paths are given to a client that may run elsewhere
        self.jobs = Jobs(JobStore(root))

    def handle(self, raw: str | bytes | Any) -> dict[str, Any]:
        try:
            request = parse_request(raw)
        except BridgeError as exc:
            return error_response(exc, request_id=exc.request_id)
        except Exception as exc:
            return error_response(to_bridge_error(exc))
        command: Command | None = None
        context = Context(self.jobs, minor=request.minor)
        try:
            command = COMMANDS.get(request.command)
            if command is None or minor_of(command.since) > request.minor:
                raise _unknown_command(request)
            result = jsonable(self._run(command, request, context))
        except Exception as exc:
            return error_response(
                to_bridge_error(exc),
                request_id=request.id,
                command=request.command,
                warnings=[*request.warnings, *context.warnings],
            )
        return ok_response(request, result, context.warnings)

    def close(self, timeout: float = 300.0) -> None:
        """Stop what this bridge started: streams and scale runs are cancelled, the other jobs
        are waited for (``timeout`` seconds at most)."""
        drain(self.jobs, timeout)

    def _run(self, command: Command, request: Request, context: Context) -> Any:
        args = check_args(command, request.args, request.minor)
        options = check_options(request.options)
        context.options = options
        wants_job = bool(options.get("async"))
        if wants_job and not (command.job or command.always_job):
            raise BridgeError(
                "usage.invalid_argument",
                f"{command.name} does not run as a job: drop options.async",
            )
        if not (wants_job or command.always_job):
            return command.handler(args, context)
        if command.prepare is not None:
            command.prepare(args, context)  # a bad request leaves no failed job behind
        record = self.jobs.start(
            command.name,
            args,
            options,
            lambda job_context: command.handler(args, job_context),
            _job_context(self.jobs, options, request.minor),
            cancellable=command.cancellable,
        )
        return command.started(record) if command.started else describe(record)


def _unknown_command(request: Request) -> BridgeError:
    """``usage.unknown_command`` as the minor the request is served as would give it: the list
    names only the commands of that minor, and a command added later says which version has it."""
    known = [n for n, c in COMMANDS.items() if minor_of(c.since) <= request.minor]
    hint = f"the commands are: {', '.join(known)}"
    later = COMMANDS.get(request.command)
    if later is not None:
        hint += f"; {request.command} needs api_version {later.since}"
    return BridgeError("usage.unknown_command", f"unknown command {request.command!r}", hint)


def _job_context(
    jobs: Jobs, options: dict[str, Any], minor: int
) -> Callable[[Any, Callable[[dict[str, Any]], None]], Context]:
    def make(cancel: Any, progress: Callable[[dict[str, Any]], None]) -> Context:
        return Context(
            jobs, options=options, cancel=cancel, progress=progress, in_job=True, minor=minor
        )

    return make
