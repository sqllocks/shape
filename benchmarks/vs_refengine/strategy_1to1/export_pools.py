"""Write the baseline's reference pools (names, companies, streets, sentences, ...) as one-entry-
per-line text files into Shape's ``src/shape/builtins/strategies/pools/`` (D-10).

Run in the *baseline* venv; it only reads the baseline checkout:

    source scripts/env.sh
    "$REFENGINE_PY" benchmarks/vs_refengine/strategy_1to1/export_pools.py          # write
    "$REFENGINE_PY" benchmarks/vs_refengine/strategy_1to1/export_pools.py --check  # == baseline

Every file is read back and compared entry for entry with the baseline's tuple, so the copy is
exact (order included). ``tests/generation/test_strategies_p404b.py`` compares the digests of the
shipped files with the ones ``baseline.py`` records in the fixtures.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import _refpkg  # noqa: E402
from paths import REFENGINE_ROOT  # noqa: E402

POOLS_DIR = HERE.parents[2] / "src" / "shape" / "builtins" / "strategies" / "pools"


def baseline_pools() -> dict[str, tuple[str, ...]]:
    sys.path.insert(0, str(REFENGINE_ROOT))
    names = _refpkg.mod("engine.data.names")
    native = _refpkg.mod("engine.strategies.native")

    pools: dict[str, Any] = {
        "first_names": names.FIRST_NAMES,
        "last_names": names.LAST_NAMES,
        "company_names": names.COMPANY_NAMES,
        "street_names": names.STREET_NAMES,
        "email_domains": names.EMAIL_DOMAINS,
        "sentences": names.SENTENCES,
        "uri_paths": names.URI_PATHS,
        "uri_domains": names.URI_DOMAINS,
        "us_states": native._US_STATES,
        "us_cities": native._US_CITIES,
        "street_suffixes": tuple(native._STREET_SUFFIXES.tolist()),
    }
    return {k: tuple(str(v) for v in vs) for k, vs in pools.items()}


def render(entries: tuple[str, ...]) -> str:
    for e in entries:
        if "\n" in e or "\r" in e or not e:
            raise ValueError(f"entry cannot be stored one per line: {e!r}")
    return "".join(f"{e}\n" for e in entries)


def read(path: Path) -> tuple[str, ...]:
    return tuple(path.read_text("utf-8").split("\n")[:-1])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="compare the files with the baseline")
    a = ap.parse_args(argv)
    bad = 0
    for name, entries in baseline_pools().items():
        path = POOLS_DIR / f"{name}.txt"
        if a.check:
            same = path.exists() and read(path) == entries
            print(f"{name:16s} {len(entries):5d} {'match' if same else 'DIFFERS'}")
            bad += 0 if same else 1
            continue
        POOLS_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(render(entries), encoding="utf-8", newline="\n")
        assert read(path) == entries, name
        print(f"wrote {path.relative_to(HERE.parents[2])} ({len(entries)} entries)")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
