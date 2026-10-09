# Glossary

Look up the words used in tutorials and reports.

Status: available.

| Word | Meaning |
|---|---|
| baseline | The reviewed profile you compare current data against. |
| cardinality | The count of distinct non-null values in a column. |
| capture | The policy used when saving a profile: safe or full. |
| contract | Requirements checked against a profile. |
| content id | The hash that identifies canonical artifact content. |
| domain | A generation schema for related tables. |
| drift | A reported change between profiles that passes the comparison rules. |
| foreign key | Child columns whose values refer to a parent key. |
| null | A missing value; distinct from zero or an empty string. |
| plugin | Installed Python code providing an entry-point extension. |
| profile | Statistics and structure measured from rows. |
| safe capture | A reduced saved profile; review it before sharing. |
| schema | Table names, columns, types and relationships. |
| seed | Input that controls a deterministic generation run. |
| shape | An artifact describing data behavior; inspect its payload kind. |
| sink | An adapter that writes generated rows. |
| source | An adapter that reads rows for profiling. |
| threshold | A configured cutoff for reporting a statistical change. |

A safe capture is data minimisation, not anonymisation.

## Related

[Concepts](CONCEPTS.md) · [Read the report](READ_REPORT.md)
