"""Unique artifact folders: ``<outputDir>/<name>/<run>/``, one per run.

A run folder is named by its UTC start time to the microsecond, ``20260930T120000123456Z``, so
the folders of one name sort in the order the runs began (folders from before this naming,
``20260930T120000Z``, sort after the runs of their own second and before the next one) and parse
with :func:`parse_run_folder`. The name is claimed rather than assumed free: a folder that
exists is never reused, a run that finds its name taken gets the next ``_2``, ``_3`` ... suffix,
so two runs never write into one folder and a baseline is never overwritten.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

__all__ = ["claim_run_folder", "parse_run_folder", "run_stamp", "unique_run_name"]

_RUN = re.compile(r"(\d{8}T\d{6})(\d{6})?Z(?:_(\d+))?")


def run_stamp(now: datetime | None = None) -> str:
    """``20260930T120000123456Z``: the UTC time of ``now`` (default: now) to the microsecond."""
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    return moment.strftime("%Y%m%dT%H%M%S%fZ")


def parse_run_folder(name: str) -> datetime | None:
    """The start time a run folder's name records (UTC), or None when it is not a run folder.

    Both the current name and the earlier one-second name are understood.
    """
    match = _RUN.fullmatch(name)
    if match is None:
        return None
    second, micro, _ = match.groups()
    try:
        moment = datetime.strptime(second, "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
    except ValueError:  # the right shape, but no such date or time (month 13, 30 February)
        return None
    return moment.replace(microsecond=int(micro)) if micro else moment


def _suffixed(stamp: str, attempt: int) -> str:
    return stamp if attempt == 1 else f"{stamp}_{attempt}"


def claim_run_folder(parent: str | Path, *, now: datetime | None = None) -> Path:
    """Create and return a new run folder below ``parent`` that no other run owns.

    ``mkdir`` without ``exist_ok`` is the claim: it fails when the folder exists, so this holds
    between processes too.
    """
    root = Path(parent)
    root.mkdir(parents=True, exist_ok=True)
    stamp = run_stamp(now)
    attempt = 1
    while True:
        path = root / _suffixed(stamp, attempt)
        try:
            os.mkdir(path)
        except FileExistsError:
            attempt += 1
            continue
        return path


def unique_run_name(
    parent: str, exists: Callable[[str], bool], *, now: datetime | None = None
) -> str:
    """A run folder name that ``exists`` does not find below ``parent`` (a URL or a path).

    For stores without an exclusive create; the check and the write are not atomic, which at
    microsecond resolution leaves only simultaneous starts of the same name.
    """
    stamp = run_stamp(now)
    attempt = 1
    while exists(f"{parent.rstrip('/')}/{_suffixed(stamp, attempt)}"):
        attempt += 1
    return _suffixed(stamp, attempt)
