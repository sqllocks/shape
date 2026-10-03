"""Recorded interactions: capture a conversation with a service once, replay it in every test.

A *tape* is the ordered list of requests a writer made and the answers it got, for one channel:

* **HTTP** (Kusto management, queries and ingestion): :class:`TapeTransport`, a ``transport``
  callable for :class:`shape_fabric.kusto.KustoClient`;
* **ODBC** (SQL database and Warehouse): :class:`TapeConnection`, a DB-API connection whose cursors
  record ``execute`` / ``executemany`` (statement and parameters), ``fetchone``, ``rowcount``,
  ``commit`` and ``rollback``.

Recording wraps a real (or fake) service: every call goes through and is written down. Replaying
answers from the tape, and fails with :class:`ReplayMismatch` at the first request that differs
from the recorded one: the writer's wire output (statements, commands, JSON bodies) is pinned.

**Secrets never reach a tape.** Every string is passed through :func:`scrub` as it is recorded
(bearer tokens, JWTs, passwords, account and shared-access keys, SAS signatures, client secrets,
private keys, ``Authorization`` headers), and :func:`save` refuses to write a tape in which
:func:`find_secrets` still finds something. The test suite scans every committed tape with the
same function.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import re
from collections.abc import Callable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

FORMAT = 1
KEPT_HEADERS = ("content-type", "accept", "authorization")


class RecordingError(RuntimeError):
    """A tape could not be written (it would hold a secret) or read."""


class ReplayMismatch(AssertionError):
    """The code under test made a request that differs from the recorded one."""


# --- secrets -----------------------------------------------------------------------------

REDACTED = "<redacted>"
_JWT = r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"
_KEYS = (
    r"(?:pwd|password|accountkey|sharedaccesskey|sharedaccesssignature|sig|client_secret|secret)"
)
# The value after a secret's key, in each form it is written: an ODBC braced value (``}}`` is a
# literal ``}``), a JSON string escaped inside another JSON string (a body on a tape), a quoted
# string, or a bare value: after ``=`` it runs to the next separator (an ODBC value may hold
# spaces), after ``:`` it is one word.
_VALUE = (
    r"\{(?:[^}]|\}\})*\}"
    r'|\\"(?:[^"\\]|\\[^"])*\\"'
    r'|"(?:[^"\\]|\\.)*"'
    r"|'[^']*'"
    r"|[^;&\s\"',}\\][^;&\r\n\"',}\\]*"
)


def _secret_value(match: re.Match[str]) -> str:
    """The key and separator kept, the value redacted inside the quotes it was written in."""
    key, close, sep, value = match.groups()
    if REDACTED in value:
        return match.group(0)
    for quote in ('\\"', '"', "'"):
        if value.startswith(quote) and len(value) > len(quote):
            return f"{key}{close}{sep}{quote}{REDACTED}{quote}"
    if "=" not in sep:
        # ``key: value`` is prose or a header: the value is one word, and the rest is kept.
        word = re.match(r"\S+", value)
        rest = value[word.end() :] if word else ""
        return f"{key}{close}{sep}{REDACTED}{rest}"
    return f"{key}{close}{sep}{REDACTED}"  # ``key=value``: an ODBC value runs to the next ``;``


_PATTERNS: list[tuple[re.Pattern[str], str | Callable[[re.Match[str]], str]]] = [
    (re.compile(r"(?i)(bearer\s+)(?!<redacted>)[A-Za-z0-9._~+/=-]{8,}"), rf"\1{REDACTED}"),
    (re.compile(_JWT), REDACTED),
    (
        # The key may be a quoted JSON key (``"password": ...``, escaped on a tape).
        re.compile(rf"(?i)\b({_KEYS})(\\?[\"']?)(\s*[=:]\s*)({_VALUE})"),
        _secret_value,
    ),
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(-----END [A-Z ]*PRIVATE KEY-----|$)"
        ),
        REDACTED,
    ),
    (re.compile(r"\b[A-Za-z0-9_.~-]{3}8Q~[A-Za-z0-9_.~-]{20,}"), REDACTED),  # Entra client secret
    (re.compile(r"[A-Za-z0-9+/]{64,}={0,2}"), REDACTED),  # a long base64 blob (a storage key)
]
_AUTH_HEADER = re.compile(r"(?i)^authorization$")


def scrub(text: str) -> str:
    """``text`` with every recognised secret replaced by ``<redacted>``."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def find_secrets(text: str) -> list[str]:
    """The secrets still readable in ``text``: nothing that :func:`scrub` would change, apart
    from what is already ``<redacted>``."""
    found: list[str] = []
    for pattern, _ in _PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(0)
            if REDACTED not in value:
                found.append(value[:12] + "...")
    return found


# --- values ------------------------------------------------------------------------------


def readable_body(body: str) -> str:
    """``body`` with the inline base64 parts of a Fabric item definition decoded.

    A long base64 run looks like a storage key, so the scrubber would blank a notebook's payload
    and the tape could no longer tell one notebook from another. The decoded text is what the
    payload says (and the scrubber still reads it)."""
    try:
        doc = json.loads(body)
        parts = doc["definition"]["parts"]
    except (ValueError, KeyError, TypeError):
        return body
    if not isinstance(parts, list):
        return body
    for part in parts:
        if isinstance(part, dict) and part.get("payloadType") == "InlineBase64":
            try:
                part["payloadType"] = "InlineText"
                part["payload"] = base64.b64decode(part["payload"]).decode("utf-8")
            except (ValueError, KeyError):
                return body
    return json.dumps(doc)


def jsonable(value: Any) -> Any:
    """``value`` as plain JSON data, strings scrubbed; types JSON lacks become ``{"$type": ...}``
    objects so that equal values compare equal after a round trip."""
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else {"$float": str(value)}
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, bytes):
        return {"$bytes": base64.b64encode(value).decode("ascii")}
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if isinstance(value, dt.datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, dt.date):
        return {"$date": value.isoformat()}
    if isinstance(value, dt.time):
        return {"$time": value.isoformat()}
    if isinstance(value, Mapping):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return {"$repr": scrub(repr(value))}


# --- the tape ----------------------------------------------------------------------------


class Tape:
    """Records ``(request, response)`` steps, or replays them (``steps`` given)."""

    def __init__(
        self, steps: list[dict[str, Any]] | None = None, *, channel: str, scenario: str = ""
    ) -> None:
        self.channel = channel
        self.scenario = scenario
        self.replaying = steps is not None
        self.steps: list[dict[str, Any]] = list(steps or [])
        self._pos = 0

    def step(self, request: Any, produce: Callable[[], Any] | None = None) -> Any:
        request = jsonable(request)
        if self.replaying:
            if self._pos >= len(self.steps):
                raise ReplayMismatch(
                    f"{self.scenario}: unexpected extra request #{self._pos + 1}: "
                    f"{json.dumps(request)[:400]}"
                )
            expected = self.steps[self._pos]
            self._pos += 1
            if expected["request"] != request:
                raise ReplayMismatch(
                    f"{self.scenario}: request #{self._pos} differs from the recording\n"
                    f"  recorded: {json.dumps(expected['request'])[:600]}\n"
                    f"  actual:   {json.dumps(request)[:600]}"
                )
            return expected["response"]
        assert produce is not None, "recording needs the call to make"
        response = jsonable(produce())
        self.steps.append({"request": request, "response": response})
        return response

    def assert_done(self) -> None:
        if self.replaying and self._pos != len(self.steps):
            raise ReplayMismatch(
                f"{self.scenario}: {len(self.steps) - self._pos} recorded request(s) "
                "were never made"
            )

    def document(self, source: str, result: Any = None) -> dict[str, Any]:
        return {
            "format": FORMAT,
            "channel": self.channel,
            "scenario": self.scenario,
            "source": source,
            "steps": self.steps,
            "result": jsonable(result),
        }


def save(path: Path, document: Mapping[str, Any]) -> None:
    """Write a tape document; refuses (and writes nothing) if a secret survived scrubbing."""
    text = json.dumps(document, indent=2, sort_keys=True) + "\n"
    leaked = find_secrets(text)
    if leaked:
        raise RecordingError(f"refusing to write {path.name}: it would hold a secret ({leaked[0]})")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def load(path: Path) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("format") != FORMAT:
        raise RecordingError(f"{path.name}: unknown tape format {doc.get('format')!r}")
    return doc


def replay_tape(doc: Mapping[str, Any]) -> Tape:
    return Tape(list(doc["steps"]), channel=str(doc["channel"]), scenario=str(doc["scenario"]))


# --- HTTP --------------------------------------------------------------------------------


class TapeTransport:
    """A Kusto ``transport`` that records calls to ``inner``, or replays them from the tape."""

    def __init__(self, tape: Tape, inner: Callable[..., Any] | None = None) -> None:
        self.tape = tape
        self._inner = inner

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes, timeout: float
    ) -> tuple[int, dict[str, str], bytes]:
        kept = {
            k.lower(): ("Bearer " + REDACTED if _AUTH_HEADER.match(k) else v)
            for k, v in headers.items()
            if k.lower() in KEPT_HEADERS
        }
        request = {
            "method": method,
            "url": url,
            "headers": kept,
            "body": readable_body(body.decode("utf-8", "replace")),
        }

        def produce() -> dict[str, Any]:
            assert self._inner is not None
            try:
                status, resp_headers, data = self._inner(method, url, headers, body, timeout)
            except Exception as exc:
                return {"raises": type(exc).__name__, "message": str(exc)}
            data = _hide_vault_value(url, data)
            return {
                "status": status,
                "headers": dict(resp_headers),
                "body": data.decode("utf-8", "replace"),
            }

        response = self.tape.step(request, produce if not self.tape.replaying else None)
        if "raises" in response:
            raise {"ConnectionError": ConnectionError, "TimeoutError": TimeoutError}.get(
                response["raises"], RuntimeError
            )(response.get("message", ""))
        return int(response["status"]), dict(response["headers"]), response["body"].encode("utf-8")


def _hide_vault_value(url: str, data: bytes) -> bytes:
    """A Key Vault answer holds the secret itself: the tape keeps the shape of the answer, never
    the value (the caller gets ``<redacted>`` in the recording as well, so both runs agree)."""
    if ".vault.azure.net/secrets/" not in url:
        return data
    try:
        doc = json.loads(data)
    except ValueError:
        return data
    if isinstance(doc, dict) and "value" in doc:
        doc["value"] = REDACTED
        return json.dumps(doc).encode()
    return data


# --- ODBC --------------------------------------------------------------------------------


class _TapeCursor:
    def __init__(self, tape: Tape, inner: Any) -> None:
        self._tape = tape
        self._inner = inner
        self._fast = False

    def _do(self, request: dict[str, Any], call: Callable[[], Any]) -> Any:
        def produce() -> dict[str, Any]:
            try:
                return {"value": call()}
            except Exception as exc:
                return {"raises": type(exc).__name__, "message": str(exc)}

        response = self._tape.step(request, None if self._tape.replaying else produce)
        if "raises" in response:
            raise RuntimeError(response.get("message", response["raises"]))
        return response["value"]

    def execute(self, sql: str, *params: Any) -> _TapeCursor:
        self._do(
            {"op": "execute", "sql": sql, "params": list(params)},
            lambda: _void(self._inner.execute(sql, *params)),
        )
        return self

    def executemany(self, sql: str, rows: Any) -> None:
        rows = [list(r) for r in rows]
        self._do(
            {"op": "executemany", "sql": sql, "rows": rows},
            lambda: _void(self._inner.executemany(sql, rows)),
        )

    def fetchone(self) -> Any:
        value = self._do({"op": "fetchone"}, lambda: _row(self._inner.fetchone()))
        return tuple(value) if value is not None else None

    @property
    def rowcount(self) -> int:
        return int(self._do({"op": "rowcount"}, lambda: self._inner.rowcount))

    @property
    def fast_executemany(self) -> bool:
        return self._fast

    @fast_executemany.setter
    def fast_executemany(self, value: bool) -> None:
        self._fast = bool(value)
        self._do(
            {"op": "set", "name": "fast_executemany", "value": bool(value)},
            lambda: setattr(self._inner, "fast_executemany", bool(value)),
        )


def _void(_result: Any) -> None:
    return None


def _row(row: Any) -> list[Any] | None:
    return None if row is None else list(row)


class TapeConnection:
    """A DB-API connection that records calls to ``inner``, or replays them from the tape."""

    def __init__(self, tape: Tape, inner: Any = None) -> None:
        self.tape = tape
        self._inner = inner

    def cursor(self) -> _TapeCursor:
        return _TapeCursor(self.tape, self._inner.cursor() if self._inner is not None else None)

    def _do(self, op: str, call: Callable[[], Any]) -> None:
        self.tape.step(
            {"op": op},
            None if self.tape.replaying else (lambda: {"value": call()}),
        )

    def commit(self) -> None:
        self._do("commit", lambda: self._inner.commit())

    def rollback(self) -> None:
        self._do("rollback", lambda: self._inner.rollback())

    def close(self) -> None:
        self._do("close", lambda: self._inner.close())
