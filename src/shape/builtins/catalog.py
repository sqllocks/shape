"""The built-ins and the entry point each one registers under (strings only: importing this
module loads no built-in code).

``pyproject.toml`` declares the same table as ``[project.entry-points]``;
``tests/plugins/test_builtins.py`` fails if the two drift apart.
"""

from __future__ import annotations

# (entry-point group, name, "module:Class")
BUILTINS: tuple[tuple[str, str, str], ...] = (
    ("shape.sources", "csv", "shape.builtins.sources:CsvSource"),
    ("shape.sources", "parquet", "shape.builtins.sources:ParquetSource"),
    ("shape.sources", "jsonl", "shape.builtins.sources:JsonlSource"),
    ("shape.sources", "ipc", "shape.builtins.sources:IpcSource"),
    ("shape.sources", "abfss", "shape.builtins.sources:AbfssSource"),
    ("shape.sources", "delta", "shape.builtins.sources:DeltaSource"),
    ("shape.sinks", "csv", "shape.builtins.sinks:CsvSink"),
    ("shape.sinks", "parquet", "shape.builtins.sinks:ParquetSink"),
    ("shape.sinks", "jsonl", "shape.builtins.sinks:JsonlSink"),
    ("shape.sinks", "ipc", "shape.builtins.sinks:IpcSink"),
    ("shape.sinks", "tsv", "shape.builtins.sinks:TsvSink"),
    ("shape.sinks", "sql", "shape.builtins.sinks:SqlSink"),
    ("shape.sinks", "excel", "shape.builtins.sinks:ExcelSink"),
    ("shape.sinks", "delta", "shape.builtins.sinks:DeltaSink"),
    ("shape.detectors", "email", "shape.builtins.detectors:EmailDetector"),
    ("shape.detectors", "us_ssn", "shape.builtins.detectors:UsSsnDetector"),
    ("shape.detectors", "phone", "shape.builtins.detectors:PhoneDetector"),
    ("shape.detectors", "ipv4", "shape.builtins.detectors:Ipv4Detector"),
    ("shape.fitters", "auto", "shape.builtins.fitters:AutoFitter"),
    ("shape.strategies", "constant", "shape.builtins.strategies:Constant"),
    ("shape.strategies", "sequence", "shape.builtins.strategies:Sequence"),
    ("shape.strategies", "choice", "shape.builtins.strategies:Choice"),
    ("shape.strategies", "uniform", "shape.builtins.strategies:Uniform"),
    ("shape.strategies", "normal", "shape.builtins.strategies:Normal"),
    ("shape.strategies", "address", "shape.builtins.strategies:AddressStrategy"),
    ("shape.distributions", "normal", "shape.builtins.distributions:Normal"),
    ("shape.distributions", "uniform", "shape.builtins.distributions:Uniform"),
    ("shape.distributions", "exponential", "shape.builtins.distributions:Exponential"),
    ("shape.distributions", "lognormal", "shape.builtins.distributions:Lognormal"),
    ("shape.calendars", "us_federal", "shape.builtins.calendars:UsFederalCalendar"),
    ("shape.calendars", "us_retail", "shape.builtins.calendars:UsRetailCalendar"),
)
