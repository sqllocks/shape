"""Shape plugin: thin, optional adapters for tools teams already use.

* ``shape.commands``: ``lineage`` (OpenLineage), ``mlflow`` (MLflow) and ``evaluate``
  (SDMetrics, Anonymeter).
* ``shape.detectors``: ``presidio``.
* ``shape.sources``: ``duckdb``; :func:`shape_integrations.ibis.connect` for notebooks.

Importing this package imports none of the third-party libraries; each adapter loads its own
when it runs, and exits 2 with the pip command when the extra is missing.
"""

from .extras import EXTRAS, MissingExtraError

SHAPE_API = "1.0"

__all__ = ["EXTRAS", "SHAPE_API", "MissingExtraError"]
