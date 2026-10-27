#!/usr/bin/env bash
# Resumable final sweep for the FlyWord revision.
#
# Safe to kill at any time and safe to re-run after a reboot: `flyread evaluate`
# checkpoints every completed (seed, condition) pair in
# runs/<seed-id>/seed_conditions.jsonl, and `flyread sweep` skips any seed whose
# results.json already exists. Re-running this script therefore resumes rather
# than restarts.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Keep the machine awake for the multi-day run. `-s` only applies on AC power.
if [[ "${FLYWORD_CAFFEINATED:-0}" != "1" ]] && command -v caffeinate >/dev/null 2>&1; then
  export FLYWORD_CAFFEINATED=1
  exec caffeinate -i -m -s "$0" "$@"
fi

# Brian2's Cython build needs the SDK libc++ headers on this machine.
export CPLUS_INCLUDE_PATH="${CPLUS_INCLUDE_PATH:-/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk/usr/include/c++/v1}"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

PY="$ROOT/.venv/bin/python"
WORKERS="${WORKERS:-10}"
LOGDIR="$ROOT/runs/logs"
PIDFILE="$ROOT/runs/.final-sweeps.pid"
mkdir -p "$LOGDIR"

# Single-instance guard. Exit 75 (EX_TEMPFAIL) so a launchd KeepAlive job
# retries later instead of treating the collision as success.
if [[ -f "$PIDFILE" ]]; then
  old="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [[ -n "$old" ]] && kill -0 "$old" 2>/dev/null; then
    echo "[$(date '+%F %T')] already running (pid $old); exiting"
    exit 75
  fi
  rm -f "$PIDFILE"
fi
echo $$ > "$PIDFILE"
trap 'rm -f "$PIDFILE"' EXIT

run_sweep() {
  local name="$1"
  shift
  if [[ -f "$ROOT/runs/${name}-merged/results.json" ]]; then
    echo "[$(date '+%F %T')] $name already complete; skipping"
    return 0
  fi
  echo "[$(date '+%F %T')] START $name (workers=$WORKERS)"
  "$PY" -m flyread "$@" --run-id "$name" sweep --workers "$WORKERS" \
    >> "$LOGDIR/$name.log" 2>&1
  echo "[$(date '+%F %T')] DONE $name"
}

# Headline runs first so partial results are useful if the job is stopped.
run_sweep recode-v2-rng          --config configs/odor-recode.yaml
run_sweep recode-50-v2-rng       --config configs/odor-recode-50.yaml
run_sweep recode-50-controls-rng --config configs/odor-recode-50-controls.yaml

# Cheap shuffled/no-growth isolation controls (one condition each).
run_sweep recode-v2-extra-rng   --config configs/odor-recode.yaml \
  --set 'evaluation.conditions=["shuffled_labels_no_growth"]'
run_sweep recode-50-extra-rng   --config configs/odor-recode-50.yaml \
  --set 'evaluation.conditions=["shuffled_labels_no_growth"]'

# Physiological references and probes.
run_sweep odor-v1-rng  --config configs/default.yaml
run_sweep pctrl-sep-rng --config configs/odor-separable.yaml

echo "[$(date '+%F %T')] ALL SWEEPS COMPLETE"
