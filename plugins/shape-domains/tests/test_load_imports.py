"""Loading a packaged domain to generate it does not import the composite machinery: that is
imported when a domain's ``composition()`` is asked for (``shape composite``)."""

from __future__ import annotations

import subprocess
import sys

PROBE = """
import sys
from shape.generation.domains import load_domain
load_domain("hr", mode="star")
print("shape.generation.composite" in sys.modules)
from shape.plugins.host import default_host
presets = default_host().get("shape.domains", "hr").composition().presets
print("shape.generation.composite" in sys.modules, len(presets))
"""


def test_a_domain_loads_without_the_composite_module_and_still_composes() -> None:
    done = subprocess.run(
        [sys.executable, "-c", PROBE], capture_output=True, text=True, check=True, timeout=120
    )
    assert done.stdout.split() == ["False", "True", "6"], done.stdout + done.stderr
