"""Shape plugin: dbt.

File-based and dbt-Core-generic: nothing here imports or runs dbt.

* ``shape.commands``: ``from-dbt`` (a dbt project's sources, seeds or models and their tests as
  a generation schema), ``to-dbt-tests`` (a contract or a profile as ``schema.yml`` data tests),
  ``dbt-seeds`` (generate a schema's tables as dbt seeds) and ``dbt-report`` (one report for a
  dbt run and a Shape check and drift comparison).

* ``shape.sinks``: ``dbt-seeds`` writes a table as ``seeds/<table>.csv`` with a ``seeds:`` block
  of column types (``dbt://PROJECT_DIR``).

PyYAML loads only when a ``schema.yml`` is read or written.
"""

from .commands import DbtReport, DbtSeeds, FromDbt, ToDbtTests
from .seeds import DbtSeedsSink

SHAPE_API = "1.0"

__all__ = ["SHAPE_API", "DbtReport", "DbtSeeds", "DbtSeedsSink", "FromDbt", "ToDbtTests"]
