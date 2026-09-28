"""Minimal out-of-process JSON-lines plugin runtime.

OS sandboxing remains deployment-specific; this runtime provides process isolation,
timeouts, capability authorization and bounded request/response sizes.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

from shape.errors import ShapeSecurityError

from .core import PluginManifest, PluginPolicy


@dataclass(frozen=True, slots=True)
class PluginResponse:
    value: object


def invoke(
    command: list[str],
    manifest: PluginManifest,
    policy: PluginPolicy,
    request: object,
    timeout: float = 5.0,
    max_bytes: int = 1_000_000,
) -> PluginResponse:
    policy.authorize(manifest)
    raw = (json.dumps(request, separators=(",", ":")) + "\n").encode()
    if len(raw) > max_bytes:
        raise ShapeSecurityError("plugin request exceeds size limit")
    try:
        p = subprocess.run(
            command,
            input=raw,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise ShapeSecurityError("plugin timed out") from e
    if p.returncode != 0:
        raise ShapeSecurityError(f"plugin failed with exit code {p.returncode}")
    if len(p.stdout) > max_bytes:
        raise ShapeSecurityError("plugin response exceeds size limit")
    try:
        value = json.loads(p.stdout.decode().strip())
    except Exception as e:
        raise ShapeSecurityError("invalid plugin response") from e
    return PluginResponse(value)
