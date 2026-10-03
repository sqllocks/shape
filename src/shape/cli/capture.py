"""``--capture``, ``--k``, ``--column-k`` and ``--classify``: what ``shape profile`` writes (W1-11).

The default is the safe capture (:mod:`shape.privacy.redact`); ``--capture full`` keeps real
values, says so in the artifact and prints one warning on standard error.
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from shape.privacy.redact import CaptureConfig

# Kept literal so building the parser imports nothing heavy (T-18); a test pins them to
# shape.privacy.redact.MODES / DEFAULT_MODE and shape.privacy.safe_profile.K_DEFAULT.
MODES = ("safe", "full")
DEFAULT_MODE = "safe"
K_DEFAULT = 5


def add_capture_args(p: argparse.ArgumentParser, *, k: bool = True) -> None:
    """The capture options; ``k=False`` when the command already has its own ``--k``."""
    p.add_argument(
        "--capture",
        choices=MODES,
        default=DEFAULT_MODE,
        help="what is written: safe (default) keeps statistics and formats only for a sensitive "
        "column and category values only where each category has at least k rows; full keeps "
        "real values and must not be committed or shared",
    )
    if k:
        p.add_argument(
            "--k",
            type=int,
            metavar="N",
            help=f"with --capture safe: the minimum rows behind a released category (default "
            f"{K_DEFAULT})",
        )
    p.add_argument(
        "--column-k",
        action="append",
        default=[],
        metavar="COLUMN=N",
        help="with --capture safe: the minimum for one column (repeatable)",
    )
    p.add_argument(
        "--classify",
        action="append",
        default=[],
        metavar="COLUMN=LEVEL",
        help="with --capture safe: the declared classification of a column (PUBLIC, INTERNAL, "
        "CONFIDENTIAL, SECRET, TOP_SECRET; PII and SENSITIVE rank as CONFIDENTIAL); CONFIDENTIAL "
        "or higher makes the column sensitive (repeatable)",
    )


def _pairs(items: list[str], flag: str, what: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        name, sep, value = item.rpartition("=")
        if not sep or not name or not value:
            raise ValueError(f"{flag} expects COLUMN={what}, got {item!r}")
        out[name] = value
    return out


def config_from_args(a: argparse.Namespace) -> CaptureConfig:
    """The capture settings of the command line; ``ValueError`` (exit 2) on a bad one."""
    from shape.privacy.redact import CaptureConfig

    given = a.k is not None or a.column_k or a.classify
    if a.capture == "full":
        if given:
            raise ValueError("--k, --column-k and --classify apply to --capture safe only")
        return CaptureConfig(mode="full")
    if a.k is not None and a.k < 1:
        raise ValueError(f"--k must be an integer of at least 1, not {a.k}")
    column_k: dict[str, int] = {}
    for name, text in _pairs(a.column_k, "--column-k", "N").items():
        if not text.isdigit():
            raise ValueError(f"--column-k expects COLUMN=N, got {name}={text}")
        column_k[name] = int(text)
    classifications = _pairs(a.classify, "--classify", "LEVEL")
    return CaptureConfig(
        mode="safe",
        k=K_DEFAULT if a.k is None else a.k,
        column_k=column_k,
        classifications=classifications,
    )


def warn_full(output: Any) -> None:
    """The one warning of a full capture, on standard error."""
    from shape.privacy.redact import FULL_CAPTURE_WARNING

    print(FULL_CAPTURE_WARNING.format(output=output), file=sys.stderr)
