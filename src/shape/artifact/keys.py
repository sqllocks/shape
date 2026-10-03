"""Where signing keys come from and how private keys are stored (issue #38).

A key *source* is a string: ``-`` (standard input), ``env://NAME``, ``file://PATH`` or
``kv://...`` (see :mod:`shape.security.credrefs`), or a plain file path. A private key is either
the legacy raw form (32 bytes, base64, one line) or a passphrase-protected PKCS#8 PEM
(``ENCRYPTED PRIVATE KEY``, written by default). A passphrase comes from an environment variable,
standard input or a prompt, never from a command-line argument.

Nothing here prints, logs or puts key material or a passphrase in an error message.
"""

from __future__ import annotations

import base64
import binascii
import getpass
import os
import sys
import warnings
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import IO

from shape.security import credrefs

KEY_BYTES = 32
DEFAULT_PASSPHRASE_ENV = "SHAPE_KEY_PASSPHRASE"
STDIN = "-"

Passphrase = bytes | str | None
PassphraseSource = Passphrase | Callable[[], Passphrase]


class PassphraseRequired(ValueError):
    """The private key is encrypted and no passphrase was given."""


class UnencryptedKeyWarning(UserWarning):
    """An unencrypted private key was written."""


class KeyFilePermissionWarning(UserWarning):
    """The operating system cannot restrict the private key file to its owner by mode bits."""


def decode_raw_key(text: str, what: str) -> bytes:
    try:
        raw = base64.b64decode("".join(text.split()), validate=True)
    except (binascii.Error, ValueError) as e:
        raise ValueError(f"{what} is not a base64-encoded Ed25519 key") from e
    if len(raw) != KEY_BYTES:
        raise ValueError(f"{what} must decode to {KEY_BYTES} bytes, got {len(raw)}")
    return raw


def _as_bytes(p: Passphrase) -> bytes | None:
    if p is None:
        return None
    return p.encode("utf-8") if isinstance(p, str) else bytes(p)


def is_encrypted(text: str) -> bool:
    return "-----BEGIN ENCRYPTED PRIVATE KEY-----" in text


def encode_private_key(private_key: bytes, passphrase: Passphrase) -> str:
    """The file text of a private key: encrypted PEM with a passphrase, else the legacy raw line."""
    pw = _as_bytes(passphrase)
    if pw is None:
        return base64.b64encode(private_key).decode() + "\n"
    from shape.security.crypto import encrypt_private_key_pem

    return encrypt_private_key_pem(private_key, pw).decode("ascii")


def decode_private_key(text: str, passphrase: PassphraseSource, what: str) -> bytes:
    """The raw key in ``text`` (legacy raw, PKCS#8 PEM or encrypted PKCS#8 PEM).

    ``passphrase`` may be a callable, asked only when the key is encrypted, so an unencrypted
    key never triggers a prompt."""
    if "-----BEGIN" not in text:
        return decode_raw_key(text, what)
    pw: bytes | None = None
    if is_encrypted(text):
        value = passphrase() if callable(passphrase) else passphrase
        pw = _as_bytes(value)
        if not pw:
            raise PassphraseRequired(
                f"{what} is encrypted and needs a passphrase: set {DEFAULT_PASSPHRASE_ENV} or "
                "--passphrase-env VAR, pipe it with --passphrase-stdin, or run interactively"
            )
    from shape.security.crypto import load_private_key_pem

    try:
        return load_private_key_pem(text.encode("ascii"), pw)
    except UnicodeEncodeError:
        raise ValueError(f"{what} is not a PEM private key") from None
    except ValueError as e:
        raise ValueError(f"{what}: {e}") from None


def read_source(
    source: str | os.PathLike[str],
    what: str,
    *,
    stdin: IO[str] | None = None,
    private: bool = True,
) -> str:
    """The text a key source points to. ``-`` reads standard input."""
    s = os.fspath(source)
    if s == STDIN:
        return credrefs.read_stdin_text(stdin)
    if credrefs.is_reference(s):
        return credrefs.resolve_reference(s, private=private)
    try:
        return Path(s).read_text(encoding="ascii")
    except UnicodeDecodeError:
        raise ValueError(f"{what} {s} is not a text key file") from None


def load_private_key(
    source: str | os.PathLike[str],
    passphrase: PassphraseSource = None,
    *,
    stdin: IO[str] | None = None,
) -> bytes:
    """The raw private key behind ``source`` (see the module doc for the forms)."""
    s = os.fspath(source)
    what = "private key from standard input" if s == STDIN else f"private key {s}"
    return decode_private_key(read_source(s, what, stdin=stdin), passphrase, what)


def load_public_key(source: str | os.PathLike[str], *, stdin: IO[str] | None = None) -> bytes:
    """A public key file written by ``shape keygen``, or any other key source."""
    s = os.fspath(source)
    what = "public key from standard input" if s == STDIN else f"public key {s}"
    return decode_raw_key(read_source(s, what, stdin=stdin, private=False), what)


def read_passphrase(
    *,
    env: str | None = None,
    use_stdin: bool = False,
    prompt: str | None = None,
    confirm: bool = False,
    environ: Mapping[str, str] | None = None,
    stdin: IO[str] | None = None,
    ask: Callable[[str], str] | None = None,
    interactive: bool | None = None,
) -> bytes | None:
    """A passphrase from, in order: the variable ``env`` (error when named but unset), the first
    line of standard input, ``SHAPE_KEY_PASSPHRASE``, then a prompt (only when attached to a
    terminal). ``None`` when none is available.

    There is no argument form on purpose: a command line is visible to every user of the host and
    kept in shell history and process listings."""
    environment = os.environ if environ is None else environ
    if env:
        value = environment.get(env)
        if not value:
            raise ValueError(f"passphrase variable {env} is not set or is empty")
        return value.encode("utf-8")
    if use_stdin:
        src = stdin if stdin is not None else sys.stdin
        line = src.readline().rstrip("\r\n")
        if not line:
            raise ValueError("no passphrase on standard input")
        return line.encode("utf-8")
    default = environment.get(DEFAULT_PASSPHRASE_ENV)
    if default:
        return default.encode("utf-8")
    tty = sys.stdin.isatty() if interactive is None else interactive
    if prompt and tty:
        reader = ask or getpass.getpass
        first = reader(prompt)
        if not first:
            raise ValueError("the passphrase must not be empty")
        if confirm and reader("Repeat passphrase: ") != first:
            raise ValueError("the passphrases do not match")
        return first.encode("utf-8")
    return None


def write_keypair(
    prefix: str | os.PathLike[str],
    passphrase: Passphrase = None,
    *,
    unencrypted: bool = False,
    generate: Callable[[], tuple[bytes, bytes]],
) -> tuple[Path, Path]:
    """Write ``<prefix>.key`` and ``<prefix>.pub`` for a freshly generated pair.

    The private key is encrypted with ``passphrase``; with none, ``unencrypted=True`` must say so
    and a warning follows. The private key file is created with mode 0600 where the OS has mode
    bits. Never overwrites an existing file."""
    pw = _as_bytes(passphrase)
    if pw is not None and not pw:
        raise ValueError("the passphrase must not be empty")
    if pw is None and not unencrypted:
        raise ValueError(
            "a passphrase is required to protect the private key: give one "
            f"({DEFAULT_PASSPHRASE_ENV}, --passphrase-env, --passphrase-stdin or the prompt), or "
            "ask for an unencrypted key explicitly (--no-passphrase)"
        )
    if pw is not None and unencrypted:
        raise ValueError("a passphrase and an unencrypted key were both asked for")
    priv, pub = Path(f"{prefix}.key"), Path(f"{prefix}.pub")
    for p in (priv, pub):
        if p.exists():
            raise FileExistsError(f"refusing to overwrite the existing file {p}")
    sk, pk = generate()
    body = encode_private_key(sk, pw)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(priv, flags, 0o600)
    except FileExistsError:
        raise FileExistsError(f"refusing to overwrite the existing file {priv}") from None
    try:
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(body)
    except BaseException:
        priv.unlink(missing_ok=True)
        raise
    try:
        with open(pub, "x", encoding="ascii") as fh:
            fh.write(base64.b64encode(pk).decode() + "\n")
    except FileExistsError:
        priv.unlink(missing_ok=True)
        raise FileExistsError(f"refusing to overwrite the existing file {pub}") from None
    except BaseException:
        priv.unlink(missing_ok=True)
        raise
    if os.name == "nt":
        warnings.warn(
            f"{priv}: Windows does not apply file mode 0600; restrict the file to your account "
            "with its ACL (icacls) or keep the key encrypted",
            KeyFilePermissionWarning,
            stacklevel=3,
        )
    if pw is None:
        warnings.warn(
            f"{priv} is an UNENCRYPTED private key: anyone who can read the file can sign as you",
            UnencryptedKeyWarning,
            stacklevel=3,
        )
    return priv, pub
