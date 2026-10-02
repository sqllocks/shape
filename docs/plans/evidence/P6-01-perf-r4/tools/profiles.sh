#!/usr/bin/env bash
# Profiles of the fixed cost and the per-call cost, final tree against the round-3 tree. Run from the repo root.
set -u
cd "$(git rev-parse --show-toplevel)"
source scripts/env.sh
EVID=docs/plans/evidence/P6-01-perf-r4/profile
mkdir -p $EVID
OLD_PP="$HOME/wt-start/src:$HOME/wt-start/plugins/shape-domains/src"
PY="$SHAPE_VENV/bin/python"
T=docs/plans/evidence/P6-01-perf-r4/tools
T3=docs/plans/evidence/P6-01-perf-domains/tools
# 1. the fixed cost of the product path (round 3's floor.py: load_domain, Engine, generate+write; one row per table and medium)
{ echo "== round-3 tree"; env PYTHONPATH="$OLD_PP" $PY $T3/floor.py hr manufacturing real_estate insurance; echo "== final tree"; $PY $T3/floor.py hr manufacturing real_estate insurance; } > $EVID/floor_before_after.txt 2>&1
# 2. per-call cost of single columns at 10 rows (the fixed cost of a column) and per-row cost at the real chunk size
for tree in old new; do
  if [ $tree = old ]; then PP="$OLD_PP"; else PP=""; fi
  { for spec in "hr employee employment_status" "hr employee email" "hr employee phone" "hr employee hire_date" "hr employee department_id" "hr employee employee_id" "hr compensation base_salary" "healthcare claim filing_date"; do set -- $spec; printf "%-34s" "$2.$3"; env PYTHONPATH="$PP" $PY $T/colprof.py $1 $2 $3 10 2>&1 | head -1; done; } > $EVID/per_call_10_rows_$tree.txt 2>&1
  { env PYTHONPATH="$PP" $PY $T3/strat_bench.py insurance premium_payment 65536 2>&1 | head -8; env PYTHONPATH="$PP" $PY $T3/strat_bench.py telecom usage_record 50000 2>&1 | head -8; } > $EVID/per_row_big_chunks_$tree.txt 2>&1
done
# 3. one cold run on the final tree: timeline of the threads and tables
for d in hr insurance education; do $PY $T/timeline.py $d > $EVID/timeline_$d.txt 2>&1; done
# 4. warm single-thread cost of the columns at the real chunk size (final tree)
for d in hr insurance healthcare financial; do $PY $T/cols.py $d > $EVID/columns_warm_$d.txt 2>&1; done
# 5. CPU split of a run: user, sys, minor faults
for d in hr insurance; do for i in 1 2 3 4 5; do $PY $T/ru.py $d; done > $EVID/cpu_faults_$d.txt 2>&1; done
echo "profiles done" >> docs/plans/evidence/P6-01-perf-r4/driver.log
