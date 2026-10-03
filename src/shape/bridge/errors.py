"""Exceptions to bridge error codes (P6-11).

The table is ordered, most specific first. Only the exception's type decides the code, never its
text. An exception that is not in the table is a bug: it becomes ``internal.error``, which names
the exception type, and the traceback goes to the log (standard error), never to the client.
"""

from __future__ import annotations

import json
import logging
import sys
import zipfile
from contextlib import contextmanager
from typing import TYPE_CHECKING

from shape.bridge.protocol import BridgeError

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger("shape.bridge")


def _describe(exc: BaseException) -> str:
    from shape.cli.errors import describe

    return describe(exc)


def _is(exc: BaseException, module: str, *names: str) -> bool:
    """True when ``exc`` is an instance of one of ``names`` in ``module``, without importing a
    module that is not loaded already (an exception of a module never imported cannot be one)."""
    loaded = sys.modules.get(module)
    if loaded is None:
        return False
    return any(
        isinstance(getattr(loaded, name, None), type) and isinstance(exc, getattr(loaded, name))
        for name in names
    )


def to_bridge_error(exc: BaseException) -> BridgeError:
    """The :class:`BridgeError` for any exception."""
    if isinstance(exc, BridgeError):
        return exc
    message = _describe(exc)
    if _is(exc, "shape.generation.domains", "DomainNotFoundError"):
        return BridgeError("input.unknown_domain", message, "run the `list` command")
    if _is(exc, "shape.scale.jobs", "JobNotFoundError"):
        return BridgeError("input.unknown_job", f"no job {message}", "run `job_list`")
    if _is(exc, "shape.scale.jobs", "JobStateError"):
        return BridgeError("input.job_state", message)
    if _is(exc, "shape.artifact.io", "ArtifactSignatureError"):
        return BridgeError("policy.signature_invalid", message)
    if _is(exc, "shape.registry.local", "RawProfileError"):
        return BridgeError(
            "privacy.raw_values_withheld", message, "pass options.include_raw_values to allow it"
        )
    if _is(exc, "shape.proposals.model", "RuleConflictError"):
        return BridgeError("input.contract_conflict", message)
    if _is(exc, "shape.errors", "ShapeSecurityError") or _is(
        exc, "shape.security.hardening", "SecurityError"
    ):
        return BridgeError("policy.not_permitted", message)
    if _is(exc, "shape.errors", "ShapeCapabilityError") or isinstance(
        exc, ImportError | NotImplementedError
    ):
        return BridgeError(
            "policy.capability_unavailable",
            message,
            "install the extra the message names, or use another command",
        )
    if _is(exc, "shape.builtins.sources._azure_auth", "AuthError") or _is(
        exc, "shape.security.credrefs", "CredentialReferenceError"
    ):
        return BridgeError("auth.missing_credentials", message)
    if _is(exc, "shape.scale.http", "HttpError"):
        status = int(getattr(exc, "status", 0))
        if status in (401, 403):
            return BridgeError("auth.rejected", message, "check the token and its permissions")
        return BridgeError("io.sink_failed", message)
    if _is(exc, "shape.scale.sinks.base", "SinkError") or _is(
        exc, "shape.io.multi_store", "MultiStoreError"
    ):
        return BridgeError("io.sink_failed", message)
    if isinstance(exc, FileNotFoundError):
        return BridgeError("input.not_found", message)
    if isinstance(exc, json.JSONDecodeError | UnicodeDecodeError | zipfile.BadZipFile):
        return BridgeError("input.invalid_schema", message)
    if isinstance(exc, OSError):
        return BridgeError("io.read_failed", message)
    if isinstance(exc, KeyError):
        return BridgeError("input.invalid_value", message)
    if isinstance(exc, ValueError | TypeError | LookupError | RecursionError) or _is(
        exc, "shape.errors", "ShapeError"
    ):
        schemaish = (
            ("shape.errors", ("ShapeSchemaError", "ShapeTypeError")),
            ("shape.generation.schema", ("GenSchemaError",)),
            ("shape.quality.gatespec", ("GateSchemaError",)),
            ("shape.quality.verifyconfig", ("VerifyConfigError",)),
            ("shape.contracts.v1", ("ContractError",)),
            ("shape.spec.model", ("ModelError",)),
            ("shape.artifact.io", ("ArtifactError",)),
            ("shape.generation.ddl", ("DdlError",)),
            ("shape.io.readers", ("ReaderError",)),
            ("shape.profile.reference.sources", ("SourceError",)),
        )
        if any(_is(exc, mod, *names) for mod, names in schemaish):
            return BridgeError("input.invalid_schema", message)
        return BridgeError("input.invalid_value", message)
    logger.error("bridge internal error: %s: %s", type(exc).__name__, exc, exc_info=exc)
    return BridgeError(
        "internal.error",
        f"{type(exc).__name__}: {' '.join(str(exc).split())}",
        "this is a bug in Shape: please report it with the request that caused it",
    )


@contextmanager
def writing() -> Iterator[None]:
    """Inside it an ``OSError`` is ``io.write_failed`` (the default reading of one is a read)."""
    try:
        yield
    except OSError as exc:
        if isinstance(exc, FileNotFoundError) and exc.filename is None:
            raise
        raise BridgeError("io.write_failed", _describe(exc)) from exc
