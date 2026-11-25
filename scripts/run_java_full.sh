#!/usr/bin/env bash
# Execute all selected Java cases, one cycle, in each of four scenarios.
set -uo pipefail
arm="${1:-baseline}"
mode="${2:-}"
if [[ "$arm" != baseline && "$arm" != vckg ]] || [[ -n "$mode" && "$mode" != --dry-run ]]; then
  echo 'Usage: bash scripts/run_java_full.sh baseline|vckg [--dry-run]' >&2
  exit 2
fi
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir" || exit 1
run_stamp="$(TZ=Europe/Moscow date +%Y%m%d-%H%M%S)-$$"
output_dir="results/java-ab/full-${arm}-${run_stamp}"
status=0
for scenario in gen gen-hints fix fix-hints; do
  echo "Scenario: $scenario"
  runner_args=(--config "configs/full/java-qwen35-${arm}-${scenario}.yaml")
  if [[ "$mode" == --dry-run ]]; then
    runner_args+=(--dry-run)
  else
    runner_args+=(--output "$output_dir/$scenario")
  fi
  if .venv/bin/python -u scripts/run_java_ab.py "${runner_args[@]}"; then
    echo "Scenario completed: $scenario"
  else
    status=1
    echo "Scenario reported errors: $scenario; continuing with remaining scenarios" >&2
  fi
done
if [[ "$mode" != --dry-run ]]; then
  echo "Results: $output_dir/{gen,gen-hints,fix,fix-hints}/summary.json"
fi
exit "$status"
