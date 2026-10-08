"""Consumer data contracts (W3-13): what a consuming team depends on, checked in the producer's
CI. See ``docs/CONSUMER_CONTRACTS.md``."""

from __future__ import annotations

from .check import FORMAT as CHECK_FORMAT
from .check import VERSION as CHECK_VERSION
from .check import build_report, check_consumers, find_files, load_all, render_text
from .contract import (
    FORMAT,
    VERSION,
    ConsumerContract,
    ConsumerContractError,
    load,
    parse,
    problems,
    schema,
)

__all__ = [
    "CHECK_FORMAT",
    "CHECK_VERSION",
    "FORMAT",
    "VERSION",
    "ConsumerContract",
    "ConsumerContractError",
    "build_report",
    "check_consumers",
    "find_files",
    "load",
    "load_all",
    "parse",
    "problems",
    "render_text",
    "schema",
]
