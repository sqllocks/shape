"""Shape as Fabric User Data Functions (template; logic in ``shape.integrations.fabric.udf``).

Six functions, all camelCase parameters and ``dict`` results:

* ``profileLakehouseFile``   profile a CSV / Parquet / JSONL file in the lakehouse Files area
* ``profileLakehouseTable``  profile a lakehouse table through the SQL endpoint (row-capped)
* ``checkProfile``           check a saved ``.shape`` profile against a contract
* ``diffProfiles``           diff two saved ``.shape`` profiles
* ``profileDataFrame``       profile inline data (request limit 4 MB)
* ``generateSample``         generate rows of one table of a domain (response capped under 30 MB)

Results are safe by default: the raw ``min`` and ``max`` of a column the safe-profile gate
classifies (a personal-data pattern such as an email or SSN, or nearly every value distinct), and
the observed values of check violations and diff changes about it, are ``null`` with
``"redacted": true``. ``includeRawValues = true`` returns the raw values.

Paste this file into the User Data Functions item with the Shape wheel as a private library
(see ``requirements.md``). It only declares the Fabric bindings; the work, the size guards and
the error rules (COMPLETION_PLAN section 12.4, DM-5 and DM-6) live in the wheel.
"""

import fabric.functions as fn
import pandas as pd

from shape.integrations.fabric import udf as shape_udf

udf = fn.UserDataFunctions()

# The connection alias is the literal "shapeLakehouse" in every @udf.connection: the portal reads
# it from this file statically, so it cannot come from a constant. It equals
# shape.integrations.fabric.udf.LAKEHOUSE_ALIAS (the tests check it); to use another alias, change
# all four.


@udf.connection("shapeLakehouse", "lakehouse")
@udf.function()
def profileLakehouseFile(
    lakehouse: fn.FabricLakehouseClient,
    filePath: str,
    outputPath: str = "",
    maxMegabytes: int = 50,
    includeRawValues: bool = False,
) -> dict:
    """Profile a CSV, Parquet or JSONL file under the lakehouse Files area.

    ``filePath`` is relative to Files/. ``outputPath`` (optional) receives the ``.shape``
    artifact. Files above ``maxMegabytes`` are refused: use the notebook. The raw ``min`` and
    ``max`` of classified columns are withheld unless ``includeRawValues`` is true.
    """
    return shape_udf.profile_lakehouse_file(
        lakehouse, filePath, outputPath, maxMegabytes, includeRawValues
    )


@udf.connection("shapeLakehouse", "lakehouse")
@udf.function()
def profileLakehouseTable(
    lakehouse: fn.FabricLakehouseClient,
    tableName: str,
    maxRows: int = 1000000,
    outputPath: str = "",
    includeRawValues: bool = False,
) -> dict:
    """Profile the first ``maxRows`` rows of a lakehouse table via its SQL endpoint.

    ``sampled`` is true when the table has more rows than ``maxRows``: the profile then covers
    only the first ``maxRows`` rows the endpoint returned, not a random sample. The raw ``min``
    and ``max`` of classified columns are withheld unless ``includeRawValues`` is true.
    """
    return shape_udf.profile_lakehouse_table(
        lakehouse, tableName, maxRows, outputPath, includeRawValues
    )


@udf.connection("shapeLakehouse", "lakehouse")
@udf.function()
def checkProfile(
    lakehouse: fn.FabricLakehouseClient,
    profilePath: str,
    contract: dict,
    failOnViolation: bool = False,
    includeRawValues: bool = False,
) -> dict:
    """Check a saved ``.shape`` profile against a contract (COMPLETION_PLAN section 12.3).

    Observed values of classified columns are withheld unless ``includeRawValues`` is true.
    """
    return shape_udf.check_profile(
        lakehouse, profilePath, contract, failOnViolation, includeRawValues
    )


@udf.connection("shapeLakehouse", "lakehouse")
@udf.function()
def diffProfiles(
    lakehouse: fn.FabricLakehouseClient,
    baselinePath: str,
    currentPath: str,
    failOnDrift: bool = False,
    includeRawValues: bool = False,
) -> dict:
    """Diff two saved ``.shape`` profiles with the section 12.3 default thresholds.

    Values of classified columns are withheld unless ``includeRawValues`` is true.
    """
    return shape_udf.diff_profiles(
        lakehouse, baselinePath, currentPath, failOnDrift, includeRawValues
    )


@udf.function()
def profileDataFrame(data: pd.DataFrame, includeRawValues: bool = False) -> dict:
    """Profile inline data. The request body is limited to 4 MB.

    The raw ``min`` and ``max`` of classified columns are withheld unless ``includeRawValues``
    is true.
    """
    return shape_udf.profile_data_frame(data, includeRawValues)


@udf.function()
def generateSample(domain: str, table: str, rows: int = 10000, seed: int = 42) -> pd.DataFrame:
    """Generate ``rows`` rows of ``table`` from the domain ``domain``.

    ``rows`` is capped so the response stays under 30 MB; the same arguments always return the
    same rows. ``domain`` and ``table`` must be installed names (letters, digits, underscores).
    """
    return shape_udf.generate_sample(domain, table, rows, seed)
