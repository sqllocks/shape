#!/usr/bin/env bash
set -u
cd "$(git rev-parse --show-toplevel)"
source scripts/env.sh
EVID=docs/plans/evidence/P6-01-perf-r4/retail
OLD_PP="$HOME/wt-start/src:$HOME/wt-start/plugins/shape-domains/src"
mkdir -p "$EVID"
PY="$SHAPE_VENV/bin/python"
B=benchmarks/vs_spindle/domain_1to1
env PYTHONPATH="$OLD_PP" $PY $B/bench.py --impl shape --domain retail --scales large --runs 3 --warmup 1 --report $EVID/bench_gen_in_retail_large_before.json > $EVID/bench_gen_in_retail_large_before.txt 2>&1; echo "gen-in large before exit $?" >> $EVID/exit_codes.txt
$PY $B/bench.py --impl shape --domain retail --scales large --runs 3 --warmup 1 --report $EVID/bench_gen_in_retail_large_after.json > $EVID/bench_gen_in_retail_large_after.txt 2>&1; echo "gen-in large after exit $?" >> $EVID/exit_codes.txt
env PYTHONPATH="$OLD_PP" $PY $B/bench_cli.py --domain retail --scales medium,large --runs 5 --warmup 1 --report $EVID/bench_gen_cli_retail_before.json > $EVID/bench_gen_cli_retail_before.txt 2>&1; echo "gen-cli before exit $?" >> $EVID/exit_codes.txt
$PY $B/bench_cli.py --domain retail --scales medium,large --runs 5 --warmup 1 --report $EVID/bench_gen_cli_retail_after.json > $EVID/bench_gen_cli_retail_after.txt 2>&1; echo "gen-cli after exit $?" >> $EVID/exit_codes.txt
"$SPINDLE_PY" $B/verify.py --domain retail --scale medium --impl shape --cli --no-generate --out $EVID/verify_shape_retail_medium_cli_after.json > $EVID/verify_shape_retail_medium_cli_after.txt 2>&1; echo "retail medium cli verify exit $?" >> $EVID/exit_codes.txt
echo "retail phase done" >> docs/plans/evidence/P6-01-perf-r4/driver.log
