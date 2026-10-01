#!/bin/bash
# usage: ab.sh dataset reps   (interleaved base / new, fresh processes, harness worker)
cd /home/user/shape; source scripts/env.sh
S=/tmp/claude-0/-home-user-shape/5235a82c-2acd-5853-8953-c692f9859fe3/scratchpad
ds=$1; n=$2
for i in $(seq $n); do
  for v in base new; do :
    if [ $v = base ]; then pp=$S/base/src; else pp=/home/user/shape/src; fi
    s=$(taskset -c ${CPUS:-0-3} env PYTHONPATH=$pp $SHAPE_VENV/bin/python benchmarks/vs_spindle/profile_1to1/bench.py --worker shape $ds | python3 -c "import sys,json;print(json.loads(sys.stdin.read().strip().splitlines()[-1])['seconds'])")
    echo "$v $s"
  done
done | sort | awk '{a[$1]=a[$1]" "$2} END{for(k in a)print k":"a[k]}'
