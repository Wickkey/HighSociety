#!/bin/bash
# Overnight launcher for rl_train.py.
#
#   ./run_training.sh runs/sp1                  # new run (warm start from runs/imitation.pt)
#   ./run_training.sh runs/sp1 --iters 3000     # any rl_train.py setting can be added
#
# - keeps the Mac awake while it runs (caffeinate -i; keep it plugged in -- closing the lid can still sleep it)
# - if training crashes it waits 60 s and resumes from runs/.../latest.pt (at most MAX_RESTARTS times);
#   crash tracebacks go to train.log and the dashboard shows them; setup problems (exit code 2) aren't retried
# - Ctrl-C stops it cleanly: the current state is saved first, and it does not restart
# - running it again on the same folder continues where it left off
#
# Watch it: open runs/sp1/dashboard.html (reloads itself), or: tail -f runs/sp1/train.log

RUN="$1"; shift
if [ -z "$RUN" ]; then echo "usage: $0 runs/<name> [rl_train.py options]"; exit 2; fi
PY="${PY:-$HOME/opt/anaconda3/envs/ptorch/bin/python}"
MAX_RESTARTS=20
cd "$(dirname "$0")" || exit 1
mkdir -p "$RUN"

trap 'echo "launcher: stopped"; exit 130' INT TERM

restarts=0
while true; do
    if [ -f "$RUN/latest.pt" ]; then resume=(--resume); else resume=(); fi
    caffeinate -i "$PY" rl_train.py --run "$RUN" "${resume[@]}" "$@"
    code=$?
    if [ $code -eq 0 ]; then
        echo "launcher: done"
        break
    fi
    if [ $code -eq 3 ]; then
        continue                     # planned restart to free memory (rl_train.py --restart-every): resume now
    fi
    if [ $code -eq 2 ]; then
        echo "launcher: setup problem (see the message above) -- not retrying"
        exit 2
    fi
    restarts=$((restarts + 1))
    if [ $restarts -gt $MAX_RESTARTS ]; then
        echo "[$(date +%H:%M:%S)] launcher: giving up after $MAX_RESTARTS restarts" | tee -a "$RUN/train.log"
        exit 1
    fi
    echo "[$(date +%H:%M:%S)] launcher: rl_train.py exited with code $code -- restart $restarts/$MAX_RESTARTS in 60 s" \
        | tee -a "$RUN/train.log"
    sleep 60
done
