"""``shape simulate``: run a simulator on a domain's generated tables (P6-04).

::

    shape simulate file-drop retail --from 2024-01-01 --to 2024-01-31 -o landing/
    shape simulate stream retail --table order --sink file -o events.jsonl
    shape simulate workflow --preset order_fulfillment --entities 1000 -o out/

Each simulator is a sub-command, registered by a handler module listed in ``HANDLER_MODULES``;
a handler module has ``register(sub)``, which adds its parsers and gives each one a ``run``
default (a function of the parsed arguments returning the exit code). The target of a simulator
that works on tables is a domain name or a generation schema file; the tables are generated at
``--scale`` and ``--seed`` and handed to the simulator.

Nothing heavy loads at import time: the handlers import Arrow, the engine and the simulators
when they run.
"""

from __future__ import annotations

import importlib
from typing import Any

SHAPE_API = "1.0"

HANDLER_MODULES = [
    "shape_simulation.cli_batch",
]
"""The modules that add simulators to ``shape simulate``, one line each."""


class SimulateCommand:
    """``shape simulate KIND ...``."""

    name = "simulate"
    help = "run a simulator (file drop, SCD2 drop, stream, hybrid, workflow, ...) on generated data"

    def configure(self, parser: Any) -> None:
        sub = parser.add_subparsers(dest="simulate_cmd", required=True, metavar="KIND")
        for module in HANDLER_MODULES:
            importlib.import_module(module).register(sub)

    def run(self, args: Any) -> int:
        return int(args.run(args))


def generate_tables(target: str, scale: str | None, seed: int | None) -> dict[str, Any]:
    """The tables of a domain (or schema file) at ``scale`` and ``seed``, as Arrow tables."""
    from shape.cli.generation import load_target
    from shape.generation.engine import Engine

    schema = load_target(target)
    return dict(Engine(schema, scale=scale, seed=seed).generate().tables)
