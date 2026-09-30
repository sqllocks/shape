from typing import TYPE_CHECKING, Any

from .hardening import (
    SecurityError as SecurityError,
)
from .hardening import (
    SecurityReport as SecurityReport,
)
from .hardening import (
    classification_allows as classification_allows,
)
from .hardening import (
    enforce_no_secrets as enforce_no_secrets,
)
from .hardening import (
    inspect_shape as inspect_shape,
)
from .hardening import (
    require_no_downgrade as require_no_downgrade,
)
from .hardening import (
    scan_secrets as scan_secrets,
)
from .hardening import (
    validate_structure as validate_structure,
)
from .labels import Sensitivity as Sensitivity

if TYPE_CHECKING:
    from .crypto import (
        EncryptedPayload as EncryptedPayload,
    )
    from .crypto import (
        decrypt_aes_gcm as decrypt_aes_gcm,
    )
    from .crypto import (
        encrypt_aes_gcm as encrypt_aes_gcm,
    )
    from .crypto import (
        generate_ed25519_keypair as generate_ed25519_keypair,
    )
    from .crypto import (
        sign_ed25519 as sign_ed25519,
    )
    from .crypto import (
        verify_ed25519 as verify_ed25519,
    )

# ``crypto`` needs the optional ``cryptography`` package. It is imported only when one of
# its names is used, so that ``import shape`` works where that package is not installed.
_CRYPTO_NAMES = frozenset(
    [
        "EncryptedPayload",
        "decrypt_aes_gcm",
        "encrypt_aes_gcm",
        "generate_ed25519_keypair",
        "sign_ed25519",
        "verify_ed25519",
    ]
)


def __getattr__(name: str) -> Any:
    if name in _CRYPTO_NAMES:
        from . import crypto

        return getattr(crypto, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Sensitivity",
    "EncryptedPayload",
    "encrypt_aes_gcm",
    "decrypt_aes_gcm",
    "generate_ed25519_keypair",
    "sign_ed25519",
    "verify_ed25519",
    "SecurityError",
    "SecurityReport",
    "validate_structure",
    "scan_secrets",
    "enforce_no_secrets",
    "classification_allows",
    "require_no_downgrade",
    "inspect_shape",
]
