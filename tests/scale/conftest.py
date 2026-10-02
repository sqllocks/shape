"""Makes ``scale_schemas`` (shared schemas of the scale tests) importable: the suite runs with
``--import-mode=importlib``, which does not put a test directory on ``sys.path``."""

import sys
from pathlib import Path

HERE = str(Path(__file__).parent)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
