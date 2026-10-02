# sqllocks-shape-simulation

Shape plugin: simulation scenarios. Generates web clickstreams, financial anomalies (reversals,
fraud bursts, settlements), IoT telemetry (drift, missing readings, alert storms, fleet status),
operational logs with distributed traces, and a rideshare's telemetry and finance marts, as Arrow
tables. Deterministic: the same configuration and seed give the same tables.

```bash
pip install 'sqllocks-shape[simulation]'
shape simulate clickstream --set users=500 -o out/
```

```python
from shape_simulation.clickstream_patterns import ClickstreamConfig, ClickstreamSimulator

result = ClickstreamSimulator(ClickstreamConfig(users=500, seed=7)).run()
result.sessions, result.page_views, result.funnels, result.stats
```

Registers `shape.commands:simulate`. Its version always equals core's (`sqllocks-shape`), and it is
released together with core. Guide: `docs/plugins/simulation.md` in the repository; how plugins are
written: `docs/plugins/authoring.md`.
