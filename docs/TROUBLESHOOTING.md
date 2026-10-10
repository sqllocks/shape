# Troubleshooting by symptom

Use the symptom and exit code to find the next check.

Status: available.

## My command exits nonzero

For profile, check and diff, exit 0 means the operation passes. A check or gated diff uses 1
for a failed requirement or reported drift. Exit 2 means bad input, a missing dependency or
unavailable required evidence. Model commands have their own codes; use [Exit codes](EXIT_CODES.md)
for the command you run. Read stderr as well as stdout. The tutorials show both streams together.

## I need a diagnosis

`shape doctor` reports environment and optional dependency checks. Cloud options can probe
endpoints or authentication; a default local check is different from a cloud check.
<!-- owner: support maintainer — supply doctor transcripts for additional environments. The local checkout transcript appears on the installation page. -->
Check the installed core version and the plugin entry points before changing your source URI.

## Generation writes no files

A profile generation command without a format produces a summary and writes nothing.
Choose a format and output directory. [The generation tutorial](tutorials/05-generate-profile.md)
uses the Python API and explicitly writes the result. The [CLI reference](https://docs.shapedata.ai/reference/cli/)
shows the current writer options.

## Delta reports a reader feature error

Shape detects deletion vectors and column mapping and selects the optional DuckDB reader.
You need the delta-fallback dependencies and its Delta extension. The extension may download
on first use; that is a network call. <!-- owner: Delta maintainer — execute an extension-install and fallback transcript before publishing an installation command here. -->
For version or as-of reads, confirm that the historical files remain available.

## Safe capture is refused

A full capture or recorded unsafe marker fails safe validation. The scan also looks for
retained values and personal-data patterns. Do not suppress the refusal simply to commit a file.
Review the classification and capture policy. Multi-table dataset profiles have a known 0.9.1
validator failure; see [Known limitations](KNOWN_LIMITATIONS.md). Single-table validation is
shown in [Your first profile](tutorials/01-first-profile.md).

## A folder is rejected or has the wrong tables

Without dataset mode, a folder is one table whose files are partitions. CLI profiling requires
matching columns, in any order. Dataset mode treats files as named tables. Do not mix unrelated
files into one partition folder. A `tables` contract requires a dataset profile; a single table
cannot satisfy that input shape.

## My model query rejects a .shape file

The extension does not identify the payload kind. Queries operate on models, not profiles.
Use the saved profile's report or inspection interface; read [Models](MODELS.md).

## Related

[FAQ](FAQ.md) · [Known limitations](KNOWN_LIMITATIONS.md) · [Exit codes](EXIT_CODES.md)
