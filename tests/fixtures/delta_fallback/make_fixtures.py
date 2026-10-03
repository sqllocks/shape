"""Regenerates the Delta fixtures in this directory with Apache Spark + Delta Lake.

Writer: Apache Spark 4.2.0 with Delta Lake 4.4.0 (`pip install pyspark==4.2.0 delta-spark==4.4.0`;
the Delta jars come from Maven Central). Run: `python make_fixtures.py OUT_DIR`.
"""

import shutil
import sys

from delta import configure_spark_with_delta_pip
from pyspark.sql import SparkSession

b = (
    SparkSession.builder.master("local[1]")
    .appName("mk")
    .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
    .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
    .config("spark.ui.enabled", "false")
)
spark = configure_spark_with_delta_pip(b).getOrCreate()

out = sys.argv[1]
shutil.rmtree(out, ignore_errors=True)
spark.sql(
    f"CREATE TABLE delta.`{out}/dv` (id BIGINT, name STRING, amount DOUBLE) USING delta "
    "TBLPROPERTIES ('delta.enableDeletionVectors'='true')"
)
spark.sql(f"INSERT INTO delta.`{out}/dv` SELECT id, concat('n', id), id*1.5 FROM range(100)")
spark.sql(f"DELETE FROM delta.`{out}/dv` WHERE id % 10 = 0")
spark.sql(
    f"CREATE TABLE delta.`{out}/cm` (id BIGINT, `full name` STRING, amount DOUBLE) USING delta "
    "TBLPROPERTIES ('delta.columnMapping.mode'='name','delta.minReaderVersion'='2',"
    "'delta.minWriterVersion'='5')"
)
spark.sql(f"INSERT INTO delta.`{out}/cm` SELECT id, concat('n', id), id*1.5 FROM range(100)")
spark.sql(f"CREATE TABLE delta.`{out}/plain` (id BIGINT, name STRING, amount DOUBLE) USING delta")
spark.sql(f"INSERT INTO delta.`{out}/plain` SELECT id, concat('n', id), id*1.5 FROM range(100)")
