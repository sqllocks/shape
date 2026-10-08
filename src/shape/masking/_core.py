"""Implementation of :mod:`shape.masking`.

Every output is a pure function of (key, kind, input value): bytes come from HMAC-SHA256 in counter
mode over a length-prefixed message, so the result does not depend on the other values in a batch,
on the table or column, on the order of rows or on the process. The key lives in memory only.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import stat
from collections.abc import Callable, Mapping
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any, NoReturn

import pyarrow as pa  # type: ignore[import-untyped]

MASKING_API_VERSION = "1.0"
MIN_KEY_BYTES = 16
KINDS: tuple[str, ...] = ("identifier", "name", "email", "phone", "date", "text")

_RESERVED_DOMAINS = ("example.com", "example.org", "example.net")
_ALGORITHM = b"shape.masking.v1"
_MAX_ATTEMPTS = 24
# Kinds whose outputs identify a value: two different inputs must not share an output.
_UNIQUE_KINDS = frozenset({"identifier", "email", "phone"})


class MaskingError(ValueError):
    """A value cannot be masked safely (no format-preserving change exists, a collision)."""


class MaskingKeyError(ValueError):
    """The key is missing, too short, of the wrong type or kept unsafely."""


# ---- key handling --------------------------------------------------------------------------


def generate_key(nbytes: int = 32) -> bytes:
    """A new random key from the operating system (at least ``MIN_KEY_BYTES`` bytes)."""
    if nbytes < MIN_KEY_BYTES:
        raise MaskingKeyError(f"a key needs at least {MIN_KEY_BYTES} bytes")
    return os.urandom(nbytes)


def load_key(*, file: str | Path | None = None, env: str | None = None) -> bytes:
    """The key from a file or from an environment variable (exactly one of the two).

    A key file must not be readable by group or others (POSIX). Surrounding whitespace is dropped.
    Keep the key outside the directory that holds masked output."""
    if (file is None) == (env is None):
        raise MaskingKeyError("give exactly one of file= and env=")
    if env is not None:
        value = os.environ.get(env)
        if not value:
            raise MaskingKeyError(f"environment variable {env} is not set")
        return value.strip().encode("utf-8")
    path = Path(str(file))
    try:
        st = path.stat()
        if os.name == "posix" and st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise MaskingKeyError(
                f"key file {path} has unsafe permissions; restrict it (chmod 600)"
            )
        return path.read_bytes().strip()
    except OSError as exc:
        raise MaskingKeyError(f"cannot read key file {path}: {exc.strerror}") from exc


def _key_bytes(key: object) -> bytes:
    if isinstance(key, str):
        key = key.encode("utf-8")
    if not isinstance(key, bytes | bytearray):
        raise MaskingKeyError("the key must be bytes (or text)")
    if len(key) < MIN_KEY_BYTES:
        raise MaskingKeyError(f"the key must be at least {MIN_KEY_BYTES} bytes long")
    return bytes(key)


# ---- the keyed byte stream -----------------------------------------------------------------


class _Stream:
    """Bytes for one (kind, attempt, value), as HMAC-SHA256 blocks over a counter."""

    def __init__(self, masker: Masker, domain: str, value: str) -> None:
        d, v = domain.encode("utf-8"), value.encode("utf-8")
        self._prefix = _ALGORITHM + len(d).to_bytes(4, "big") + d + len(v).to_bytes(8, "big") + v
        self._masker = masker
        self._buf = b""
        self._counter = 0

    def _take(self, n: int) -> bytes:
        while len(self._buf) < n:
            h = self._masker._hmac()
            h.update(self._prefix + self._counter.to_bytes(4, "big"))
            self._buf += h.finalize()
            self._counter += 1
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def below(self, n: int) -> int:
        """A number in ``range(n)`` (64 bits of input: the modulo bias is below 2**-40)."""
        return int.from_bytes(self._take(8), "big") % n

    def pick(self, seq: tuple[str, ...] | list[str]) -> str:
        return seq[self.below(len(seq))]


@cache
def _pool(name: str) -> tuple[str, ...]:
    text = resources.files("shape.masking").joinpath("data", f"{name}.txt").read_text("utf-8")
    return tuple(line for line in text.split("\n") if line)


def _ascii_word(name: str) -> str:
    """``name`` as lower-case letters and digits only (an e-mail local part), never empty."""
    return re.sub(r"[^a-z0-9]", "", name.lower()) or "user"


def _style(original: str, value: str) -> str:
    if original.isupper():
        return value.upper()
    if original.islower():
        return value.lower()
    return value


def _replace_chars(value: str, s: _Stream, *, letters: bool = True, keep: int = 0) -> str:
    """Digits become digits, ASCII letters letters of the same case, other characters stay. A
    first digit (after an optional sign) that is not 0 stays non-zero, so an integer keeps its
    width. The first ``keep`` characters are copied."""
    lead = next((i for i, c in enumerate(value) if c not in "+-"), len(value))
    out: list[str] = []
    for i, c in enumerate(value):
        if i < keep:
            out.append(c)
        elif "0" <= c <= "9":
            out.append(str(1 + s.below(9)) if i == lead and c != "0" else str(s.below(10)))
        elif letters and "a" <= c <= "z":
            out.append(chr(97 + s.below(26)))
        elif letters and "A" <= c <= "Z":
            out.append(chr(65 + s.below(26)))
        else:
            out.append(c)
    return "".join(out)


# ---- kinds ---------------------------------------------------------------------------------

_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d", "%Y%m%d", "%d-%b-%Y", "%b %d, %Y")

_EMAIL_RE = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"
_SSN_RE = r"(?<![\w-])\d{3}-\d{2}-\d{4}(?![\w-])"
_IP_RE = r"(?<![\w.])\d{1,3}(?:\.\d{1,3}){3}(?![\w.])"
_PHONE_RE = r"(?<![\w.])(?:\+\d{1,3}[ -]?)?(?:\(\d{3}\)|\d{3})[ .-]?\d{3}[ .-]\d{4}(?![\w-])"
_TEXT_RE = re.compile(
    f"(?P<email>{_EMAIL_RE})|(?P<ssn>{_SSN_RE})|(?P<ip>{_IP_RE})|(?P<phone>{_PHONE_RE})"
)


def _phone_prefix(value: str) -> int:
    m = re.match(r"\+\d+", value)
    return m.end() if m else 0


class Masker:
    """Deterministic, keyed masking. All methods are pure functions of (key, kind, value)."""

    def __init__(self, key: bytes | bytearray | str) -> None:
        self._key = _key_bytes(key)
        try:
            from cryptography.hazmat.primitives import hashes, hmac
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise MaskingKeyError(
                "keyed masking needs the cryptography package: pip install 'sqllocks-shape[sign]'"
            ) from exc
        self._base = hmac.HMAC(self._key, hashes.SHA256())

    def __repr__(self) -> str:
        return f"Masker(api={MASKING_API_VERSION})"

    def __reduce__(self) -> NoReturn:
        raise TypeError("a Masker holds a secret key and cannot be pickled")

    def _hmac(self) -> Any:
        return self._base.copy()

    # ---- one value -------------------------------------------------------------------------

    def mask(self, kind: str, value: Any, **options: Any) -> Any:
        """The masked ``value`` of ``kind`` (one of ``KINDS``); ``None`` stays ``None``.

        Options: ``part`` ("full", "first" or "last") for ``name``; ``max_days`` (default 365) and
        ``subject`` for ``date``."""
        if kind not in KINDS:
            raise ValueError(f"unknown masking kind {kind!r}; use one of {', '.join(KINDS)}")
        known = {"name": {"part"}, "date": {"max_days", "subject"}}.get(kind, set())
        extra = sorted(set(options) - known)
        if extra:
            raise TypeError(f"unknown option(s) for {kind}: {', '.join(extra)}")
        if value is None:
            return None
        return getattr(self, f"_mask_{kind}")(value, **options)

    def _unique_change(self, domain: str, text: str, make: Callable[[_Stream], str]) -> str:
        """``make`` applied to a fresh stream per attempt until the result differs from ``text``."""
        for attempt in range(_MAX_ATTEMPTS):
            name = domain if attempt == 0 else f"{domain}/{attempt}"
            out = make(_Stream(self, name, text))
            if out != text:
                return out
        raise MaskingError(f"no {domain} replacement differs from the value {text[:1]}...")

    def _mask_identifier(self, value: Any) -> Any:
        text = str(value)
        out = self._unique_change("identifier", text, lambda s: _replace_chars(text, s))
        if isinstance(value, int) and not isinstance(value, bool):
            return int(out)
        return out

    def _mask_name(self, value: Any, part: str = "full") -> str:
        if part not in ("full", "first", "last"):
            raise ValueError("part must be 'full', 'first' or 'last'")
        text = str(value)

        def make(s: _Stream) -> str:
            first, last = s.pick(_pool("first_names")), s.pick(_pool("last_names"))
            if part == "first":
                new = first
            elif part == "last":
                new = last
            elif "," in text:
                new = f"{last}, {first}"
            elif len(text.split()) < 2:
                new = first
            else:
                new = f"{first} {last}"
            return _style(text, new)

        return self._unique_change(f"name/{part}", text, make)

    def _mask_email(self, value: Any) -> str:
        text = str(value).strip().lower()

        def make(s: _Stream) -> str:
            first = _ascii_word(s.pick(_pool("first_names")))
            last = _ascii_word(s.pick(_pool("last_names")))
            sep = s.pick(("", ".", "_"))
            digits = f"{s.below(1000):03d}"
            return f"{first}{sep}{last}{digits}@{s.pick(_RESERVED_DOMAINS)}"

        return self._unique_change("email", text, make)

    def _mask_phone(self, value: Any) -> str:
        text = str(value)
        keep = _phone_prefix(text)
        return self._unique_change(
            "phone", text, lambda s: _replace_chars(text, s, letters=False, keep=keep)
        )

    def _mask_date(self, value: Any, max_days: int = 365, subject: Any = None) -> Any:
        if max_days < 1:
            raise ValueError("max_days must be at least 1")
        shift = self._date_shift(str(value) if subject is None else str(subject), max_days)
        delta = dt.timedelta(days=shift)
        try:
            return self._shift_date(value, delta)
        except OverflowError:
            raise MaskingError(
                "the masked date is outside the supported range (year 1 to 9999)"
            ) from None

    @staticmethod
    def _shift_date(value: Any, delta: dt.timedelta) -> Any:
        if isinstance(value, dt.datetime | dt.date):
            return value + delta
        text = str(value).strip()
        try:
            parsed = dt.datetime.fromisoformat(text)
        except ValueError:
            for fmt in _DATE_FORMATS:
                try:
                    return (dt.datetime.strptime(text, fmt) + delta).strftime(fmt)
                except ValueError:
                    continue
            raise MaskingError("not a date in a supported format") from None
        shifted = (parsed + delta).isoformat(sep="T" if "T" in text else " ")
        return shifted[:10] if len(text) <= 10 else shifted

    def _date_shift(self, subject: str, max_days: int) -> int:
        k = _Stream(self, "date", subject).below(2 * max_days) - max_days
        return k + 1 if k >= 0 else k

    def _mask_text(self, value: Any) -> str:
        def replace(m: re.Match[str]) -> str:
            kind = m.lastgroup
            text = m.group()
            if kind == "email":
                return self._mask_email(text)
            if kind == "phone":
                return self._mask_phone(text)
            if kind == "ip":
                return self._mask_ip(text)
            return self._unique_change("identifier", text, lambda s: _replace_chars(text, s))

        return _TEXT_RE.sub(replace, str(value))

    def _mask_ip(self, text: str) -> str:
        def make(s: _Stream) -> str:
            return ".".join(str(1 + s.below(254)) for _ in range(4))

        return self._unique_change("ip", text, make)

    def _derive_seed(self, label: str) -> int:
        """A 63-bit seed for the random-number paths of ``shape mask`` (private)."""
        return _Stream(self, f"seed/{label}", "").below(1 << 63)

    # ---- columns and tables ----------------------------------------------------------------

    def mask_column(self, kind: str, values: Any, **options: Any) -> pa.Array:
        """A column masked value by value; same length, nulls and Arrow type as ``values``."""
        if isinstance(values, pa.ChunkedArray):
            arr = values.combine_chunks()
        elif isinstance(values, pa.Array):
            arr = values
        else:
            arr = pa.array(list(values))
        t = arr.type
        if not (
            pa.types.is_string(t)
            or pa.types.is_large_string(t)
            or pa.types.is_integer(t)
            or pa.types.is_date(t)
            or pa.types.is_timestamp(t)
        ):
            raise MaskingError(f"cannot mask a column of type {t}")
        cache: dict[Any, Any] = {}
        out: list[Any] = []
        for v in arr.to_pylist():
            if v is None:
                out.append(None)
                continue
            if v not in cache:
                cache[v] = self.mask(kind, v, **options)
            out.append(cache[v])
        try:
            return pa.array(out, type=t)
        except (pa.ArrowInvalid, pa.ArrowTypeError) as exc:
            raise MaskingError(f"masked {kind} values do not fit the column type {t}") from exc

    def mask_tables(
        self,
        tables: Mapping[str, pa.Table],
        columns: Mapping[str, Mapping[str, str | Mapping[str, Any]]],
    ) -> dict[str, pa.Table]:
        """Mask the named columns of ``tables``; ``columns`` maps table -> column -> kind (or a
        mapping ``{"kind": ..., **options}``). A value gets the same mask in every column of the
        same kind, so keys and the columns that refer to them still match. Tables and columns not
        named are returned as they are. Two different values of an identifying kind (identifier,
        email, phone) that would get the same mask are an error, never a silent merge."""
        specs: list[tuple[str, str, str, dict[str, Any]]] = []
        for tname, cols in columns.items():
            if tname not in tables:
                raise KeyError(f"no table {tname!r}")
            for cname, spec in cols.items():
                if cname not in tables[tname].column_names:
                    raise KeyError(f"no column {cname!r} in table {tname!r}")
                opts = dict(spec) if isinstance(spec, Mapping) else {"kind": spec}
                specs.append((tname, cname, str(opts.pop("kind")), opts))
        out = dict(tables)
        seen: dict[str, dict[Any, Any]] = {k: {} for k in _UNIQUE_KINDS}
        for tname, cname, kind, opts in specs:
            table = out[tname]
            col = table.column(cname)
            new = self.mask_column(kind, col, **opts)
            if kind in _UNIQUE_KINDS:
                self._check_distinct(kind, col, new, seen[kind], f"{tname}.{cname}")
            out[tname] = table.set_column(
                table.column_names.index(cname), table.schema.field(cname), new
            )
        return out

    @staticmethod
    def _check_distinct(
        kind: str, before: pa.ChunkedArray, after: pa.Array, seen: dict[Any, Any], label: str
    ) -> None:
        for a, b in zip(before.to_pylist(), after.to_pylist(), strict=True):
            if a is None:
                continue
            # an email is masked after strip().lower(), so its spellings are one value
            seen.setdefault(str(a).strip().lower() if kind == "email" else a, b)
        owners: dict[Any, Any] = {}
        for a, b in seen.items():
            if owners.setdefault(b, a) != a:
                raise MaskingError(
                    f"two different {kind} values get the same mask (column {label}); "
                    "the value space is too small for this kind"
                )
