cd /home/user/shape && source scripts/env.sh
D=$1; N=$2
G=benchmarks/vs_spindle/domain_1to1/generate.py
A="--impl shape --domain $D --scale medium --seed 1042"
t() { grep GEN_JSON | python3 -c "import sys,json; r=json.loads(sys.stdin.read()[9:]); print(r['total_s'], r['cpu_s'], r['minor_faults'])"; }
for i in $(seq $N); do
  echo "old $(PYTHONPATH=/tmp/wt_old/src $SHAPE_VENV/bin/python $G $A | t)"
  echo "new $($SHAPE_VENV/bin/python $G $A | t)"
done
