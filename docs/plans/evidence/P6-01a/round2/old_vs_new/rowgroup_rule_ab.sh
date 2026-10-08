cd /home/user/shape && source scripts/env.sh
D=$1; N=$2
for i in $(seq $N); do for m in fixed 4 8; do echo "$m $($SHAPE_VENV/bin/python /tmp/rg2.py $D $m /tmp/o/rg2_$D)"; done; done
