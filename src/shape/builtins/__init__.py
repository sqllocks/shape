"""Everything core ships as a plugin (plan section 4.3).

Each subpackage is one entry-point group and declares ``SHAPE_API``. The entry points are
listed in ``pyproject.toml`` and mirrored in :mod:`shape.plugins.registry`, the only module that
may import this package (an import-linter contract enforces it). Reach built-ins through the
plugin host, never by importing from here.
"""
