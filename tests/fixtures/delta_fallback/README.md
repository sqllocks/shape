# Delta fallback fixtures

Real Delta tables written by Apache Spark 4.2.0 with Delta Lake 4.4.0 (`make_fixtures.py`);
the `.crc` checksum files Spark writes beside the log were removed. No table here was written
by hand or by delta-rs.

| Table | Feature | Versions | Rows |
|---|---|---|---|
| `dv` | deletion vectors (`delta.enableDeletionVectors`) | 0 create, 1 insert 100 rows (`id` 0-99), 2 `DELETE WHERE id % 10 = 0` | 0, 100, 90 |
| `cm` | column mapping (`delta.columnMapping.mode = name`; the column `full name` has a space) | 0 create, 1 insert 100 rows | 0, 100 |
| `plain` | none (both readers read it) | 0 create, 1 insert 100 rows | 0, 100 |

All three have the columns `id BIGINT`, a string and `amount DOUBLE` (`id * 1.5`).
