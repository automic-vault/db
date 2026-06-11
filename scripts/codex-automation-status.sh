#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
automation_dir="${repo_root}/cache/automation"

printf 'workspace: %s\n' "${repo_root}"

for job in db npm-full-scan; do
  status_path="${automation_dir}/${job}.status.json"
  log_path="${automation_dir}/${job}.log"
  printf '\n== %s status ==\n' "${job}"
  if [[ -f "${status_path}" ]]; then
    cat "${status_path}"
  else
    printf 'no status yet\n'
  fi
  if [[ -f "${log_path}" ]]; then
    printf '\n-- last 40 log lines: %s --\n' "${log_path}"
    tail -40 "${log_path}"
  fi
done

printf '\n== active maintenance processes ==\n'
ps -axo pid,ppid,etime,pcpu,pmem,stat,command \
  | rg 'automation-runner|hourly-maintenance|build-db.py --refresh|generate-pkg|build-combined-json' \
  | rg -v 'rg automation-runner|rg -v|codex-automation-status|sed -n' || true
