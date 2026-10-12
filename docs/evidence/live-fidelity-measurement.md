# Live fidelity overhead — committed local measurement

Status: available.

These are recorded local runs. Commands and results are historical evidence, not commands executed by the docs harness.

Captured on **2026-10-12 UTC**, product base `8d10aa0a768383a947fa77d9547b066f8cfbd9cc`. This is a fresh real local benchmark using the repository's `benchmarks/live_fidelity/run.py`, no service or VM. The [raw measurement](live-fidelity-measurement.json) records every sample and the full machine label.

Machine: **INTEL(R) XEON(R) PLATINUM 8573C**, five visible logical/affinity CPUs, cgroup quota **4 CPU** (`400000 100000`), visible RAM **18,440,136 kB**, cgroup memory limit **16 GiB**, Linux **6.18.44**, Python **3.12.14**, PyArrow **25.0.1**, Shape **0.9.1**, rust kernel. Machine label: `owner-notes-2-linux-x86_64-2026-10-12`; THP is `madvise`. These describe this shared cloud execution environment, not a guaranteed dedicated host.

Command (BENCH_OUT_DIR points at a temporary local directory; SHAPE_VENV at the installed repository environment):

```bash
python benchmarks/live_fidelity/run.py --workloads retail:small --overhead-workload retail:medium --repeats 3 --evidence /tmp/owner-notes2-live-fidelity.json
```

Reference seed **7**, event seed **1007**, overhead workload **retail:medium**, **three** interleaved repetitions per mode/sink. Modes: no tee (`none`), tee without profiling (`tee`), tee plus profiling (`tee+profile`). Both real file and null sinks are measured. The exclusive repository benchmark lock and quiet-load gate are used without overrides. The correctness/negative-control workload is retail:small; paced validation uses retail:medium at 10,000 events/s for 20 seconds per mode. No gate is lowered, and neither --quick nor --no-overhead is used.

Complete captured harness output:

```text
retail:small             events    21,750  live 90.695  fidelity 90.695  max table diff 0.0000  control: 3 alerts, drop 16.3
overhead (file sink): none 230,684/s, tee 230,075/s, tee+profile 234,572/s; tee 0.3%, tee+profile -1.7%
overhead (null sink): none 2,586,073/s, tee 612,014/s, tee+profile 335,013/s; tee 76.3%, tee+profile 87.0%
realtime 10,000/s none         rate 10,001 (+0.01%), worst second 0.5%, lag 16.6 ms, cpu 17% of a core
realtime 10,000/s tee          rate 10,000 (+0.00%), worst second 0.5%, lag 55.1 ms, cpu 27% of a core
realtime 10,000/s tee+profile  rate 10,000 (+0.00%), worst second 0.5%, lag 28.8 ms, cpu 29% of a core
PASS
```

Python exit: **0**. Raw `passed: true`, failures empty. The first attempt printed PASS with an empty-failure report but its shell/tool session reported 1; this record uses the repeat with an explicitly captured Python exit 0. No failed benchmark predicate was suppressed or product code changed.

The negative file-sink overhead observed in one mode is measurement variation, not a claim that extra work speeds up Shape. Results are scoped to this machine/workload and are not capacity promises or measurements of Kafka/Event Hubs/cloud sinks. This supplies the committed, machine-labelled file/null overhead artifact requested by `docs/EMIT.md:537` in the baseline inventory.

## Related

[Published receipts](index.md) · [Public owner decisions](../DECISIONS.md)
