"""The emitter runtime: the deterministic event sequence of a schema, paced and delivered to a
sink at least once, with a checkpoint (P5-01)."""

from shape.streaming.emit.anomaly import (
    DEFAULT_MUTATOR,
    AnomalyInjector,
    ValueAnomalyMutator,
    resolve_mutators,
)
from shape.streaming.emit.formats import (
    ENVELOPES,
    FIELD_SEQ,
    FIELD_TABLE,
    FIELD_TIME,
    decode_line,
    encode_batch,
    event_key,
    read_events,
)
from shape.streaming.emit.rate import Burst, RateSchedule, parse_burst
from shape.streaming.emit.runtime import EmitConfig, EmitReport, EmitRunner
from shape.streaming.emit.sinks import EmitterSink, EventSink, FileSink, MemorySink, StdoutSink
from shape.streaming.emit.source import EventBlock, EventPlan

__all__ = [
    "DEFAULT_MUTATOR",
    "ENVELOPES",
    "FIELD_SEQ",
    "FIELD_TABLE",
    "FIELD_TIME",
    "AnomalyInjector",
    "Burst",
    "EmitConfig",
    "EmitReport",
    "EmitRunner",
    "EmitterSink",
    "EventBlock",
    "EventPlan",
    "EventSink",
    "FileSink",
    "MemorySink",
    "RateSchedule",
    "StdoutSink",
    "ValueAnomalyMutator",
    "decode_line",
    "encode_batch",
    "event_key",
    "parse_burst",
    "read_events",
    "resolve_mutators",
]
