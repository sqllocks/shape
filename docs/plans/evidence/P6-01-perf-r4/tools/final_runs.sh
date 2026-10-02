#!/usr/bin/env bash
# Final measurement session of the P6-01-perf round 4 lane. Run from the repo root.
#   phase verify : T-21 verifier, small and medium, every domain, on the final tree
#   phase before : GEN-IN medium on the round-3 tree (worktree on PYTHONPATH), 5 runs + 1 warm-up
#   phase after  : GEN-IN medium on the final tree, then the verifier on the timed output
set -u
cd "$(git rev-parse --show-toplevel)"
source scripts/env.sh
EVID=docs/plans/evidence/P6-01-perf-r4
OLD_PP="$HOME/wt-start/src:$HOME/wt-start/plugins/shape-domains/src"
DOMAINS="${DOMAINS:-hr real_estate pulse supply_chain insurance capital_markets education financial healthcare iot manufacturing marketing telecom retail}"
PHASE="$1"
mkdir -p "$EVID/verify" "$EVID/before" "$EVID/after" "$EVID/retail"
case "$PHASE" in
verify)
  for d in $DOMAINS; do for s in small medium; do
    rm -rf "$BENCH_OUT_DIR/shape/$d/$s"
    "$SPINDLE_PY" benchmarks/vs_spindle/domain_1to1/verify.py --domain "$d" --scale "$s" --impl shape \
      --out "$EVID/verify/verify_shape_${d}_${s}.json" > "$EVID/verify/verify_shape_${d}_${s}.txt" 2>&1
    rc=$?
    echo "$d $s verify exit $rc" >> "$EVID/verify/exit_codes.txt"
  done; done ;;
before)
  for d in $DOMAINS; do
    env PYTHONPATH="$OLD_PP" "$SHAPE_VENV/bin/python" benchmarks/vs_spindle/domain_1to1/bench.py --impl shape --domain "$d" \
      --scales medium --runs 5 --warmup 1 --report "$EVID/before/bench_gen_in_${d}.json" > "$EVID/before/bench_gen_in_${d}.txt" 2>&1
    echo "$d before bench exit $?" >> "$EVID/before/exit_codes.txt"
  done ;;
after)
  for d in $DOMAINS; do
    "$SHAPE_VENV/bin/python" benchmarks/vs_spindle/domain_1to1/bench.py --impl shape --domain "$d" \
      --scales medium --runs 5 --warmup 1 --report "$EVID/after/bench_gen_in_${d}.json" > "$EVID/after/bench_gen_in_${d}.txt" 2>&1
    echo "$d after bench exit $?" >> "$EVID/after/exit_codes.txt"
    "$SPINDLE_PY" benchmarks/vs_spindle/domain_1to1/verify.py --domain "$d" --scale medium --impl shape --no-generate \
      --out "$EVID/verify/verify_shape_${d}_medium_timed_output.json" > "$EVID/verify/verify_shape_${d}_medium_timed_output.txt" 2>&1
    echo "$d medium timed-output verify exit $?" >> "$EVID/verify/exit_codes.txt"
  done ;;
esac
echo "phase $PHASE done" >> "$EVID/driver.log"
