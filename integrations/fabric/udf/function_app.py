"""Shape as Fabric User Data Functions (template; logic in ``shape.integrations.fabric.udf``).

Five functions, all camelCase parameters and ``dict`` results:

* ``profileLakehouseFile``   profile a CSV / Parquet / JSONL file in the lakehouse Files area
* ``profileLakehouseTable``  profile a lakehouse table through the SQL endpoint (row-capped)
* ``checkProfile``           check a saved ``.shape`` profile against a contract
* ``diffProfiles``           diff two saved ``.shape`` profiles
* ``profileDataFrame``       profile inline data (request limit 4 MB)

Paste this file into the User Data Functions item with the Shape wheel as a private library
(see ``requirements.md``). It only declares the Fabric bindings; the work, the size guards and
the error rules (COMPLETION_PLAN section 12.4, DM-5 and DM-6) live in the wheel.
"""

import fabric.functions as fn
import pandas as pd

from shape.integrations.fabric import udf as shape_udf
from shape.integrations.fabric.udf import LAKEHOUSE_ALIAS

udf = fn.UserDataFunctions()


@udf.connection(LAKEHOUSE_ALIAS, "lakehouse")
@udf.function()
def profileLakehouseFile(
    lakehouse: fn.FabricLakehouseClient,
    filePath: str,
    outputPath: str = "",
    maxMegabytes: int = 50,
) -> dict:
    """Profile a CSV, Parquet or JSONL file under the lakehouse Files area.

    ``filePath`` is relative to Files/. ``outputPath`` (optional) receives the ``.shape``
    artifact. Files above ``maxMegabytes`` are refused: use the notebook.
    """
    return shape_udf.profile_lakehouse_file(lakehouse, filePath, outputPath, maxMegabytes)


@udf.connection(LAKEHOUSE_ALIAS, "lakehouse")
@udf.function()
def profileLakehouseTable(
    lakehouse: fn.FabricLakehouseClient,
    tableName: str,
    maxRows: int = 1000000,
    outputPath: str = "",
) -> dict:
    """Profile the first ``maxRows`` rows of a lakehouse table via its SQL endpoint.

    ``sampled`` is true when the table has more rows than ``maxRows``: the profile then covers
    only the first ``maxRows`` rows the endpoint returned, not a random sample.
    """
    return shape_udf.profile_lakehouse_table(lakehouse, tableName, maxRows, outputPath)


@udf.connection(LAKEHOUSE_ALIAS, "lakehouse")
@udf.function()
def checkProfile(
    lakehouse: fn.FabricLakehouseClient,
    profilePath: str,
    contract: dict,
    failOnViolation: bool = False,
) -> dict:
    """Check a saved ``.shape`` profile against a contract (COMPLETION_PLAN section 12.3)."""
    return shape_udf.check_profile(lakehouse, profilePath, contract, failOnViolation)


@udf.connection(LAKEHOUSE_ALIAS, "lakehouse")
@udf.function()
def diffProfiles(
    lakehouse: fn.FabricLakehouseClient,
    baselinePath: str,
    currentPath: str,
    failOnDrift: bool = False,
) -> dict:
    """Diff two saved ``.shape`` profiles with the section 12.3 default thresholds."""
    return shape_udf.diff_profiles(lakehouse, baselinePath, currentPath, failOnDrift)


@udf.function()
def profileDataFrame(data: pd.DataFrame) -> dict:
    """Profile inline data. The request body is limited to 4 MB."""
    return shape_udf.profile_data_frame(data)
