"""Security hardening for untrusted Shape inputs and classification boundaries."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

from shape.privacy.policy import LEVELS


class SecurityError(ValueError):
    pass


MAX_DEPTH = 64
MAX_STRING = 1_000_000
MAX_CONTAINER = 1_000_000
SECRET_PATTERNS = (
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{30,}\b")),
    # bounded run: unbounded, every repeated "Endpoint=sb://" rescanned to the end (#292)
    ("azure_connection", re.compile(r"Endpoint=sb://[^;\s]{1,512};SharedAccessKeyName=")),
    ("storage_account_key", re.compile(r"(?i)\bAccountKey=[A-Za-z0-9+/=]{20,}")),
    ("shared_access_key", re.compile(r"(?i)\bSharedAccessKey=[A-Za-z0-9+/=]{20,}")),
    ("sas_signature", re.compile(r"[?&]sig=[A-Za-z0-9%+/=]{20,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("generic_bearer", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}\b", re.I)),
    (
        "password_assignment",
        re.compile(
            r"(?i)(?:\b|_)(?:password|passwd|pwd|secret|api[_-]?key)[\"']?\s*[:=]\s*[\"']?[^\"'\s,}]{8,}"
        ),
    ),
)


def validate_structure(obj: Any, depth: int = 0, *, allow_nonfinite: bool = False) -> Any:
    """Bounds the depth, sizes and types of an untrusted document. NaN and infinity are
    refused unless ``allow_nonfinite`` (the .shape writer encodes them explicitly, P8)."""
    if depth > MAX_DEPTH:
        raise SecurityError("maximum object depth exceeded")
    if isinstance(obj, str):
        if len(obj) > MAX_STRING:
            raise SecurityError("string too large")
    elif isinstance(obj, dict):
        if len(obj) > MAX_CONTAINER:
            raise SecurityError("mapping too large")
        for k, v in obj.items():
            if not isinstance(k, str):
                raise SecurityError("non-string mapping key")
            validate_structure(k, depth + 1, allow_nonfinite=allow_nonfinite)
            validate_structure(v, depth + 1, allow_nonfinite=allow_nonfinite)
    elif isinstance(obj, (list, tuple)):
        if len(obj) > MAX_CONTAINER:
            raise SecurityError("container too large")
        for x in obj:
            validate_structure(x, depth + 1, allow_nonfinite=allow_nonfinite)
    elif isinstance(obj, float):
        if not allow_nonfinite and not math.isfinite(obj):
            raise SecurityError("non-finite number")
    elif obj is not None and not isinstance(obj, (bool, int)):
        raise SecurityError(f"unsupported value type {type(obj).__name__}")
    return obj


def scan_secrets(obj):
    text = json.dumps(obj, default=str, ensure_ascii=False)
    return tuple(name for name, p in SECRET_PATTERNS if p.search(text))


def enforce_no_secrets(obj: Any) -> Any:
    hits = scan_secrets(obj)
    if hits:
        raise SecurityError("credential material detected: " + ",".join(hits))
    return obj


def classification_allows(source, target):
    s = str(source).upper()
    t = str(target).upper()
    if s not in LEVELS or t not in LEVELS:
        raise SecurityError("unknown classification")
    return LEVELS[t] >= LEVELS[s]


def require_no_downgrade(source, target):
    if not classification_allows(source, target):
        raise SecurityError(f"classification downgrade denied: {source} -> {target}")
    return True


@dataclass(frozen=True, slots=True)
class SecurityReport:
    structure_valid: bool
    secret_patterns: tuple[str, ...]
    classification: str


def inspect_shape(shape, classification="PUBLIC"):
    validate_structure(shape)
    hits = scan_secrets(shape)
    if str(classification).upper() not in LEVELS:
        raise SecurityError("unknown classification")
    return SecurityReport(True, hits, str(classification).upper())
