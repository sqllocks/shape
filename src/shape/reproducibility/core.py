import hashlib
import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class ReproManifest:
    algorithm: str
    version: str
    seed: int
    references: tuple[str, ...] = ()
    packages: tuple[str, ...] = ()


def digest(m):
    return hashlib.sha256(json.dumps(asdict(m), sort_keys=True).encode()).hexdigest()
