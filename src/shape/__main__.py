"""``python -m shape``: the same entry point as the ``shape`` command."""

from __future__ import annotations

from shape.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
