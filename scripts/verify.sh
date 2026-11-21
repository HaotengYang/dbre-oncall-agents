#!/bin/sh
# Full verification before merging into main. Stops at the first failing step
# and exits with that step's own exit code, so the eval runner's codes
# (1 below threshold or no answer, 2 bad input, 3 lab not at baseline) reach
# the caller. "make verify" runs this too, but make turns every failure into 2.
set -u
cd "$(dirname "$0")/.."
PYTHON=.venv/bin/python
[ -x "$PYTHON" ] || PYTHON=python3

run_step() {
  step_name=$1
  shift
  echo "==> $step_name"
  "$@"
  step_status=$?
  if [ "$step_status" -ne 0 ]; then
    echo "verify: step '$step_name' failed with exit code $step_status" >&2
    exit "$step_status"
  fi
}

run_step "start the lab" make up
run_step "unit tests" "$PYTHON" -m pytest
run_step "clear leftover faults" "$PYTHON" -m fault_injector clear
run_step "quick eval set" "$PYTHON" -m eval_runner run --set quick
echo "verify: all steps passed"
