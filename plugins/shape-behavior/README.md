# sqllocks-shape-behavior

Shape plugin: **behavior models**. Declarative state-machine modules (states with delays, guards,
weighted and conditional transitions, attributes, clinical-style events and an extension point for
domain events) run for a population on a virtual clock, deterministic per seed and resumable, and
return an Arrow table of timestamped events that a domain pack turns into tables. It also imports
Generic Module Framework JSON modules that you download yourself.

```bash
pip install sqllocks-shape-behavior
shape behave run subscription --population 10000 --years 3 --seed 7 -o out/
```

```python
from shape_behavior import Population, Simulator, load_module

sim = Simulator([load_module("subscription")], Population(size=10_000, start="2024-01-01"))
events = sim.run_until("2027-01-01")      # a pyarrow.Table
```

Full reference: `docs/plugins/behavior.md` in the repository. Its version always equals core's
(`sqllocks-shape`) and it is released together with core. It depends only on numpy and pyarrow.
