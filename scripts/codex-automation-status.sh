#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
automation_dir="${repo_root}/cache/automation"
overall_status=0

status_field() {
  local status_path="$1"
  local field="$2"

  python3 - "$status_path" "$field" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
field = sys.argv[2]
if not path.is_file():
    raise SystemExit(1)
data = json.loads(path.read_text())
value = data.get(field, "")
if value is None:
    value = ""
print(value)
PY
}

job_has_active_process() {
  local job="$1"
  local pattern

  case "${job}" in
    db)
      pattern='automation-runner\.sh (db|--run-unlocked db)|hourly-maintenance\.py'
      ;;
    npm-full-scan)
      pattern='automation-runner\.sh (npm-full-scan|--run-unlocked npm-full-scan)|build-db\.py --refresh --npm-full-scan|build-combined-json\.py'
      ;;
    *)
      return 1
      ;;
  esac

  python3 - "$pattern" "$$" <<'PY'
import re
import subprocess
import sys

pattern = re.compile(sys.argv[1])
current_pid = int(sys.argv[2])

output = subprocess.check_output(["ps", "-axo", "pid=,command="], text=True)

for line in output.splitlines():
    line = line.rstrip()
    if not line:
        continue
    pid_text, _, command = line.lstrip().partition(" ")
    try:
        pid = int(pid_text)
    except ValueError:
        continue
    if pid == current_pid:
        continue
    if "codex-automation-status.sh" in command:
        continue
    if pattern.search(command):
        raise SystemExit(0)

raise SystemExit(1)
PY
}

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
  if [[ -f "${status_path}" ]]; then
    state="$(status_field "${status_path}" state || true)"
    if [[ "${state}" == "running" ]] && ! job_has_active_process "${job}"; then
      overall_status=1
      printf '\nstatus check: stale running state (no matching process)\n'
    fi
  fi
done

printf '\n== public db freshness ==\n'
if "${script_dir}/publish-public-db.py" --check-only; then
  printf 'public db freshness: ok\n'
else
  overall_status=1
  printf 'public db freshness: failed\n'
fi

printf '\n== active maintenance processes ==\n'
ps -axo pid,ppid,etime,pcpu,pmem,stat,command \
  | rg 'automation-runner|hourly-maintenance|build-db.py --refresh|generate-pkg|build-combined-json' \
  | rg -v 'rg automation-runner|rg -v|codex-automation-status|sed -n' || true

exit "${overall_status}"
