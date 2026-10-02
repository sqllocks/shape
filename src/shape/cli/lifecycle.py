"""How the process ends. ``shape.cli.main.main`` records whether it is the program (called with no
``argv``) so that ``generate`` can end the process as soon as its output is complete."""

from __future__ import annotations

import logging
import os
import sys

# Set by ``main``: it was called with no ``argv``, and nothing runs after the command returns.
quick_exit_allowed = False


def exit_now(code: int) -> None:
    """End the process now, after flushing what is buffered, when ``quick_exit_allowed``; return
    (so the caller carries on) otherwise, which is what the tests that call ``main(argv)`` get."""
    if not quick_exit_allowed:
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (OSError, ValueError):
            pass
    logging.shutdown()
    os._exit(code)
