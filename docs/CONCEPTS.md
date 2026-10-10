# Concepts

Understand the files and decisions in a Shape workflow.

Status: available.

## Profile and shape

You profile rows to describe a table's behavior: types, counts, null rates, distinct values,
distributions and detected keys. A dataset profile holds several named tables and relationships.
You save a profile in a `.shape` container and load it without reading the source again.
The extension also holds models; a model and a profile are different payloads.
[Models](MODELS.md) describes the separate model command surface.

## Safe capture

The default saved profile is a safe capture. Sensitive columns retain statistics and formats;
category values are released only when their counts meet the release threshold. The default
threshold is five rows. A full capture retains real values. A safe capture is data minimisation,
not anonymisation. Aggregates and repeated releases can still disclose facts. Review artifacts,
reports and summaries before you share them.

## Contract and drift

A contract states requirements such as a column being non-null or unique. A check judges a
profile against those requirements. Some rules need values a safe capture omits; an error or
unavailable rule result must not be treated as a pass.
Drift compares a current profile with a baseline. A threshold filters changes in a statistic;
structural changes also appear. Drift can be expected. You review it before accepting a baseline.

## Domain and plugin

A domain is a shipped generation schema with tables, relationships, row-count scales and
reference data. It gives you development rows without profiling a source. A plugin supplies
adapters or commands through Python entry points. Plugins run trusted code in your process.
Installation and allow-list controls are not a process sandbox.

## Related

[Glossary](GLOSSARY.md) · [Anatomy](ANATOMY.md) · [Known limitations](KNOWN_LIMITATIONS.md)
