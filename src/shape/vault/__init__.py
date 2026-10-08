"""The value vault (W5-03): an encrypted file of the values a safe capture withheld.

Envelope encryption with AES-256-GCM through ``cryptography`` (via ``shape.security.crypto``);
see ``docs/VAULT.md``. Needs the ``[sign]`` extra.
"""

from __future__ import annotations

from .errors import (
    KekMismatchError as KekMismatchError,
)
from .errors import (
    VaultAuthenticationError as VaultAuthenticationError,
)
from .errors import (
    VaultError as VaultError,
)
from .errors import (
    VaultFormatError as VaultFormatError,
)
from .errors import (
    VaultInputError as VaultInputError,
)
from .errors import (
    VaultMismatchError as VaultMismatchError,
)
from .errors import (
    VaultReferenceError as VaultReferenceError,
)
from .errors import (
    VaultVersionError as VaultVersionError,
)
