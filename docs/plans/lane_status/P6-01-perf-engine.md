# P6-01-perf-engine - engine-wide performance, round 3 (lane/P6-01-perf-engine)

Status: **in progress** (this file is rewritten with the final numbers before the last push).

Machine: Intel(R) Xeon(R) Processor @ 2.10GHz, 4 vCPU (KVM), 15 GiB, Python 3.11.15, numpy 2.4.6,
pyarrow 25.0.1 (both venvs), baseline 3.0.1 (`422e78df`) built by `setup_spindle.sh`.

"Before" = the branch as merged from P6-01a-d (start of this session), 14 domains at medium,
`bench.py --impl shape --domain D --scales medium --runs 5 --warmup 1`:
`docs/plans/evidence/P6-01-perf-engine/before/`.
