"""Where ``shape demo`` keeps its state: connection profiles and session manifests.

``$SHAPE_HOME`` (default ``~/.shape``) holds ``connections.json`` and ``sessions/``. Nothing is
created until something is saved.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

HOME_ENV = "SHAPE_HOME"
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def shape_home() -> Path:
    """The state directory: ``$SHAPE_HOME``, else ``~/.shape``."""
    given = os.environ.get(HOME_ENV)
    return Path(given) if given else Path.home() / ".shape"


def connections_path() -> Path:
    return shape_home() / "connections.json"


def sessions_dir() -> Path:
    return shape_home() / "sessions"


def check_name(value: str, what: str) -> str:
    """``value`` when it is a plain name (letters, digits, ``.``, ``_``, ``-``): a name is used in
    a file name, so it must not hold a path separator or start with a dot."""
    if not _NAME.match(value):
        raise ValueError(
            f"{what} {value!r} is not a plain name: use letters, digits, '.', '_' and '-' "
            "(at most 64 characters, starting with a letter or digit)"
        )
    return value
