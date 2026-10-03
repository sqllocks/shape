"""Kerberos keytab sign-in for ``mssql://`` targets (``--auth kerberos --keytab REF --principal P``).

A SQL Server that takes Windows authentication only cannot be reached with a SQL login or a
Microsoft Entra token. On Linux and macOS Shape gets a ticket the way a service does: it runs
``kinit -k -t KEYTAB -c CACHE PRINCIPAL`` (the MIT Kerberos client, ``kinit``, must be on
``PATH``) into a **private credential cache**, a file in a new mode-700 directory, and the
connection then uses ``Trusted_Connection=yes`` with ODBC Driver 18.

* The keytab is a credential reference: ``file://PATH`` (the file must be mode 600) or
  ``kv://VAULT/NAME`` (a Key Vault secret holding the keytab base64-encoded; it is decoded into a
  mode-600 temporary file that is removed as soon as ``kinit`` returns). It is never a path on
  the command line.
* ``KRB5CCNAME`` is set only while a connection is being opened (:meth:`KerberosSession.wrap`)
  and put back afterwards; the process environment is not changed otherwise. ``kinit`` gets it in
  its own environment.
* The cache is removed when the command ends, also after an error (:func:`release_all`, called by
  ``shape.cli.main``), when :meth:`KerberosSession.close` runs, and at interpreter exit.
* On Windows there is no ``kinit`` and no keytab: the connection uses the signed-in account
  (``Trusted_Connection=yes`` alone), and ``--keytab`` is an error.
* An error names ``kinit``'s message, the keytab *reference* and the principal. It never holds the
  keytab bytes or the decoded secret.
"""

from __future__ import annotations

import atexit
import base64
import binascii
import os
import shutil
import subprocess  # nosec B404 - kinit is run with an argument list, never a shell
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from shape.security import credrefs

from .errors import AuthError

KINIT_MISSING = "--auth kerberos needs the MIT Kerberos client (kinit) on PATH"
WINDOWS_KEYTAB = "on Windows, --auth kerberos uses the signed-in account; leave out --keytab"
KINIT_TIMEOUT_SECONDS = 60

_LIVE: set[KerberosSession] = set()
_ENV_LOCK = threading.RLock()  # the environment is process-wide: one connection opens at a time


class KerberosSession:
    """A ticket in a private credential cache, obtained with the keytab at construction."""

    def __init__(self, keytab: str, principal: str) -> None:
        self.keytab_ref = keytab
        self.principal = principal
        self._dir: Path | None = None
        self._secrets: list[str] = []  # what a message must never show (a decoded Key Vault secret)
        kinit = shutil.which("kinit")
        if kinit is None:
            raise AuthError(KINIT_MISSING)
        self._dir = Path(tempfile.mkdtemp(prefix="shape-krb5-"))  # mode 700
        self.cache_path = self._dir / "krb5cc"
        _LIVE.add(self)
        try:
            self._kinit(kinit)
        except BaseException:
            self.close()
            raise

    # -- sign-in ---------------------------------------------------------------------------
    def _keytab_file(self) -> tuple[str, bool]:
        """The keytab path for ``kinit`` and whether it is a temporary file to remove."""
        ref = self.keytab_ref
        scheme = credrefs.scheme_of(ref)
        if scheme == "file":
            path = ref[len("file://") :]
            try:
                credrefs.check_private_file(path)
            except credrefs.CredentialReferenceError as exc:
                raise AuthError(f"--keytab {ref}: {exc}") from None
            if not Path(path).is_file():
                raise AuthError(f"--keytab {ref}: the keytab file does not exist")
            return path, False
        if scheme == "kv":
            try:
                text = credrefs.resolve_reference(ref)
            except credrefs.CredentialReferenceError as exc:
                raise AuthError(f"--keytab {ref}: {exc}") from None
            self._secrets.append(text)
            try:
                data = base64.b64decode(text, validate=True)
            except (binascii.Error, ValueError):
                raise AuthError(
                    f"--keytab {ref}: the secret must hold the keytab base64-encoded"
                ) from None
            assert self._dir is not None
            fd, name = tempfile.mkstemp(prefix="keytab-", dir=self._dir)  # mode 600
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            return name, True
        raise AuthError("--keytab takes file://PATH or kv://VAULT/NAME")

    def _kinit(self, kinit: str) -> None:
        keytab, temporary = self._keytab_file()
        env = {**os.environ, "KRB5CCNAME": str(self.cache_path)}
        failure: str | None = None
        try:
            done = subprocess.run(  # noqa: S603  # nosec B603 - argument list, no shell
                [kinit, "-k", "-t", keytab, "-c", str(self.cache_path), self.principal],
                env=env,
                capture_output=True,
                text=True,
                timeout=KINIT_TIMEOUT_SECONDS,
                check=False,
            )
            if done.returncode != 0:
                failure = _message(done.stderr or done.stdout, self._secrets)
        except subprocess.TimeoutExpired:
            failure = f"kinit did not finish within {KINIT_TIMEOUT_SECONDS} seconds"
        except OSError as exc:
            failure = f"kinit could not be run ({type(exc).__name__})"
        finally:
            if temporary:
                Path(keytab).unlink(missing_ok=True)
        if failure is not None:
            raise AuthError(
                f"kinit failed for {self.principal} with keytab {self.keytab_ref}: {failure}"
            )
        if not self.cache_path.exists():
            raise AuthError(
                f"kinit gave no ticket for {self.principal} with keytab {self.keytab_ref}"
            )

    # -- use -------------------------------------------------------------------------------
    def wrap(self, connect: Callable[..., Any]) -> Callable[..., Any]:
        """``connect`` with ``KRB5CCNAME`` pointing at this cache for the call only."""

        def connect_with_ticket(*args: Any, **kwargs: Any) -> Any:
            with _ENV_LOCK:
                before = os.environ.get("KRB5CCNAME")
                os.environ["KRB5CCNAME"] = str(self.cache_path)
                try:
                    return connect(*args, **kwargs)
                finally:
                    if before is None:
                        os.environ.pop("KRB5CCNAME", None)
                    else:
                        os.environ["KRB5CCNAME"] = before

        return connect_with_ticket

    def close(self) -> None:
        """Remove the cache (and anything else in its directory); safe to call twice."""
        _LIVE.discard(self)
        directory, self._dir = self._dir, None
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)

    def __repr__(self) -> str:
        return f"KerberosSession(principal={self.principal!r})"


def _message(text: str, secrets: list[str]) -> str:
    """``kinit``'s own message, one line, with anything secret-looking hidden."""
    from shape.security.redact import redact_text

    for secret in secrets:
        text = text.replace(secret, "***")
    one_line = " ".join(text.split())[:300] or "no message"
    return redact_text(one_line)


def release_all() -> None:
    """Remove every credential cache this process made (end of a command, also after an error)."""
    for session in list(_LIVE):
        session.close()


atexit.register(release_all)
